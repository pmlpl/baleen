"""画像页的**只读**统计查询(2026-11-26)。

画像页右栏的每个数字都从这里来。约定(与 base.py / library.py 一致):
- 一律 owner 作用域;notes 一律 ``status='active'``(软删除,ADR §3⑤);
- **全部是真数出来的聚合,零启发式猜测。**

为什么不用 SQLAlchemy 表达式拼:jsonb 数组展开(``jsonb_array_elements``)在
SQLAlchemy 里要靠 ``table_valued`` + ``true()`` 十字连接,可读性差且容易写成
误判。这里改用参数化 ``text()`` SQL —— 每条都在真实库(62 篇/2580 块/289 轮回答)
跑通过才落进来,聚合逻辑一眼能看懂。参数一律绑定,不拼字符串。

回答方式的**三分口径**(本文件的核心概念,别写错):
- ``kb``   库内答:有 note_id 引用 → 笔记真的回答了这个问题
- ``web``  联网答:有 url 引用 → 库里没有,联网补上(这是功能,不是缺陷)
- ``self`` 自答:既无 note 也无 url → 库和网都没用上,AI 用自身知识答
  —— **只有这一类才是「库该补的内容」**。

⚠️ 早期我把三者混成「零引用 = 库的盲区」,被主人当场问出「不是会联网吗」。
实测零引用的 157 条里 89 条走了联网 —— 那是两级链在正常工作,不是盲区。
kb 与 web 有交集(一次回答既引笔记又引网页),所以三者不是互斥分区:
``self`` 单独数「两个都没有」,绝不能用 ``total - kb - web``(会算出负数)。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .base import Repo

# 画像页是概览:清单类默认折叠,展开才拉这么多条(再多的部分只报总数)。
# 为什么 30 而不是 8:折叠状态下不占高度,展开时用户是真的想看,8 条太少;
# 为什么不是无限:4000 篇死库存全拉回来既慢又没人会看完,30 条足够看出「哪类是废的」。
TOP_N = 5
LIST_LIMIT = 30
ACTIVITY_WEEKS = 8

# ── 输出行类型(仅内部使用,API 层再包 pydantic) ──
Row = dict[str, Any]


class PersonaRepo(Repo):
    """画像统计:全部只读。写操作(打主题标签)在 service 层,不经此 repo。"""

    async def _one(self, sql: str, **params: Any) -> dict[str, Any]:
        row = (await self.session.execute(text(sql), self._p(**params))).mappings().first()
        return dict(row) if row else {}

    async def _all(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        rows = (await self.session.execute(text(sql), self._p(**params))).mappings().all()
        return [dict(r) for r in rows]

    def _p(self, **extra: Any) -> dict[str, Any]:
        return {"owner": self.owner_id, **extra}

    # ── 1. 规模 ──

    async def scale(self) -> dict[str, int]:
        """规模四项:活跃笔记 / 父块 / 会话 / 提问次数。"""
        row = await self._one(
            """
            select
              (select count(*) from notes
                where owner_id = :owner and status = 'active') as notes,
              (select count(*) from chunks c join notes n on n.id = c.note_id
                where n.owner_id = :owner and n.status = 'active') as chunks,
              (select count(*) from qa_sessions
                where owner_id = :owner) as sessions,
              (select count(*) from qa_messages
                where owner_id = :owner and role = 'user') as asks
            """
        )
        return {k: int(row.get(k) or 0) for k in ("notes", "chunks", "sessions", "asks")}

    # ── 2. 来源构成 ──

    async def source_mix(self) -> dict[str, int]:
        """视频转写 vs 文档导入。

        判据 ``sources.parse_meta ? 'video_url'`` —— 视频导入写这个键(见 video 流水线)。
        视频笔记落地后就是普通 md,画像里与文档笔记完全平等;这一项只回答「我的库从哪来」。
        """
        rows = await self._all(
            """
            select case when s.parse_meta ? 'video_url' then 'video' else 'doc' end as kind,
                   count(*) as n
            from notes n join sources s on s.id = n.source_id
            where n.owner_id = :owner and n.status = 'active'
            group by 1 order by 2 desc
            """
        )
        return {r["kind"]: int(r["n"]) for r in rows}

    # ── 3. 质量构成 ──

    async def quality_mix(self) -> dict[str, int]:
        """core / normal / reference / junk;NULL 归一成 ``unknown`` 不编造。"""
        rows = await self._all(
            """
            select coalesce(quality, 'unknown') as q, count(*) as n
            from notes where owner_id = :owner and status = 'active'
            group by 1 order by 2 desc
            """
        )
        return {r["q"]: int(r["n"]) for r in rows}

    # ── 4. 回答方式三分 ──

    async def answer_mix(self) -> dict[str, int]:
        """kb / web / self / total —— **互斥三分**,加起来正好等于 total。

        口径(改过一次,别退回去):早期返回的 ``kb``/``web`` 各自数「有 note」和「有 url」,
        两者有交集(43 条回答既引笔记又引网页),所以三项相加 332 > total 289,
        前端按各自除以 total 算百分比会出现 47%+37%+31%=115% 的荒唐数字。

        现在的互斥口径:
        - ``kb``  有 note 引用(不论是否也联网)——笔记真的答上了
        - ``web`` 无 note 引用、有 url 引用——库里没有,靠联网补上
        - ``self`` 两者都没有——库和网都没用上,AI 用自身知识答(这才是该补笔记的地方)
        """
        row = await self._one(
            """
            select
              count(*) filter (where has_note) as kb,
              count(*) filter (where not has_note and has_url) as web,
              count(*) filter (where not has_note and not has_url) as self,
              count(*) as total
            from (
              select
                exists(select 1 from jsonb_array_elements(m.citations) c
                       where c->>'note_id' is not null) as has_note,
                exists(select 1 from jsonb_array_elements(m.citations) c
                       where c->>'url' is not null) as has_url
              from qa_messages m
              where m.owner_id = :owner and m.role = 'assistant'
                and jsonb_typeof(m.citations) = 'array'
            ) t
            """
        )
        return {k: int(row.get(k) or 0) for k in ("kb", "web", "self", "total")}

    # ── 5. 真在用的知识 ──

    async def top_cited(self, limit: int = TOP_N) -> list[Row]:
        """被引用次数最多的笔记(只数 note 引用,网页引用是外部内容不算「我的笔记」)。"""
        return await self._all(
            """
            select n.id, n.title, count(*) as hits
            from qa_messages m, jsonb_array_elements(m.citations) c
            join notes n on n.id = (c->>'note_id')::bigint
            where m.owner_id = :owner and m.role = 'assistant'
              and c->>'note_id' is not null
              and n.owner_id = :owner and n.status = 'active'
            group by n.id, n.title
            order by hits desc, n.id
            limit :limit
            """,
            limit=limit,
        )

    # ── 6. 死库存 ──

    _DEAD_CTE = """
        with cited as (
          select distinct (c->>'note_id')::bigint as nid
          from qa_messages m, jsonb_array_elements(m.citations) c
          where m.owner_id = :owner and m.role = 'assistant'
            and c->>'note_id' is not null
        )
    """

    async def dead_notes(self, limit: int = LIST_LIMIT) -> dict[str, Any]:
        """一次都没被引用过的活跃笔记:前 N 条 + 总数(前端默认折叠,展开才看)。

        「库里有但没派上用场」——要么内容不对路,要么检索没命中。两种都值得知道。
        """
        items = await self._all(
            self._DEAD_CTE
            + """
            select n.id, n.title, coalesce(n.quality, '') as quality
            from notes n left join cited c on c.nid = n.id
            where n.owner_id = :owner and n.status = 'active' and c.nid is null
            order by n.id
            limit :limit
            """,
            limit=limit,
        )
        total_row = await self._one(
            self._DEAD_CTE
            + """
            select count(*) as total
            from notes n left join cited c on c.nid = n.id
            where n.owner_id = :owner and n.status = 'active' and c.nid is null
            """
        )
        return {"total": int(total_row.get("total") or 0), "items": items}

    # ── 7. 纯自答的问题(该补笔记的地方) ──

    async def self_answered(self, limit: int = LIST_LIMIT) -> dict[str, Any]:
        """库和网都没用上的问题:前 N 条 + **真实总数**(与死库存同形状)。

        取同会话内**上一条** user 消息,不跨会话配对。
        过滤问候语(「hi」「可以」)与过短消息 —— 那不是知识缺口。

        总数也用同一套过滤在 SQL 里数(不是 len(items)):列表有上限 30,
        若拿 len 当总数,页面上会永远显示「30」,而真实自答可能有 90 多条。
        """
        base = """
            from qa_messages a
            where a.owner_id = :owner and a.role = 'assistant'
              and jsonb_typeof(a.citations) = 'array'
              and not exists(select 1 from jsonb_array_elements(a.citations) c
                             where c->>'note_id' is not null)
              and not exists(select 1 from jsonb_array_elements(a.citations) c
                             where c->>'url' is not null)
        """
        # 过滤条件与 _is_real_question 同口径:长度 >= 6、不是纯问候、含字母数字
        real = (
            "char_length(trim(q.question)) >= 6"
            f" and lower(trim(q.question)) not in ({_SMALL_TALK_SQL})"
            " and q.question ~ '[[:alnum:]]'"
        )
        rows = await self._all(
            f"""
            with q as (
              select (select u.content from qa_messages u
                      where u.session_id = a.session_id and u.role = 'user' and u.id < a.id
                      order by u.id desc limit 1) as question,
                     a.id as aid
              {base}
            )
            select question from q where question is not null and {real}
            order by aid desc
            limit :limit
            """,
            limit=limit,
        )
        total_row = await self._one(
            f"""
            with q as (
              select (select u.content from qa_messages u
                      where u.session_id = a.session_id and u.role = 'user' and u.id < a.id
                      order by u.id desc limit 1) as question
              {base}
            )
            select count(*) as total from q where question is not null and {real}
            """
        )
        return {
            "total": int(total_row.get("total") or 0),
            "items": [
                r["question"].strip() for r in rows if _is_real_question(r.get("question") or "")
            ],
        }

    # ── 8. 活跃度 ──

    async def activity_by_week(self, weeks: int = ACTIVITY_WEEKS) -> list[Row]:
        """近 N 周每周新增笔记数。空周补 0 —— 只回有数据的周会让「停了两周」看不出来。"""
        rows = await self._all(
            """
            with series as (
              select generate_series(
                date_trunc('week', now()) - make_interval(weeks => :span - 1),
                date_trunc('week', now()),
                interval '1 week'
              )::date as week
            ),
            cnt as (
              select date_trunc('week', created_at)::date as week, count(*) as n
              from notes
              where owner_id = :owner and status = 'active'
              group by 1
            )
            select series.week, coalesce(cnt.n, 0) as n
            from series left join cnt using (week)
            order by series.week
            """,
            span=weeks,
        )
        return [{"week": str(r["week"]), "notes": int(r["n"])} for r in rows]

    # ── 9. 主题分布 ──

    async def topic_mix(self) -> list[Row]:
        """主题分布(只读 ``notes.tags``,本方法不打标)。

        标签由 ``app.service.persona.topics`` 写入 —— 那一列建表(0002)至今
        没有任何写入方,书架 ``.book-tags`` 和笔记页一直在渲染它却永远为空。
        未分析时本方法回空列表,前端据此显示「分析 N 篇笔记的主题」按钮。
        """
        return await self._all(
            """
            select t.tag as topic, count(*) as notes
            from notes n, unnest(n.tags) as t(tag)
            where n.owner_id = :owner and n.status = 'active'
            group by t.tag
            order by notes desc, topic
            """
        )

    async def untagged_titles(self) -> list[dict[str, Any]]:
        """还没打主题标签的活跃笔记(id + title),供打标用。已打标的跳过 → 增量。"""
        return await self._all(
            """
            select id, title from notes
            where owner_id = :owner and status = 'active'
              and coalesce(cardinality(tags), 0) = 0
            order by id
            """
        )


# ── 模块级小工具 ──

# 问候/承接语:不是知识缺口,不进「该补笔记」清单
_SMALL_TALK = frozenset(
    {
        "hi", "hello", "hey", "你好", "您好", "在吗", "谢谢", "多谢", "好的", "可以",
        "嗯", "好", "ok", "行", "明白", "懂了", "是的", "对", "不是", "继续", "开始",
    }
)

# SQL 里判小话用的同口径清单(:的占位符由 SQLAlchemy 自己展开,这里手工拼字面量)
_SMALL_TALK_SQL = ", ".join(f"'{word}'" for word in sorted(_SMALL_TALK))


def _is_real_question(value: str) -> bool:
    """问候/过短/纯标点 不算真提问 —— 出现在「该补笔记」清单里是噪音。"""
    cleaned = value.strip()
    if len(cleaned) < 6:
        return False
    if cleaned.lower() in _SMALL_TALK:
        return False
    return any(ch.isalnum() for ch in cleaned)
