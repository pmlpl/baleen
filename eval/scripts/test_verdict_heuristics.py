import re


def _honest_head(answer_head: str) -> bool:
    """与 negative_eval.py 判决段同款正则(单测锁行为)。"""
    return bool(
        re.search(
            r"以下用 AI 自身的知识|\[NO_KB\]|笔记库?里?(暂无|没有|未收录|没收录)"
            r"|笔记库?里.{0,40}(没有|没涉及|没找到|暂无)"
            r"|笔记库?里.{0,20}只(提到|有|讲到|给了|讲)|库里的笔记只"
            r"|现有.{0,14}只(讲|提)|按通用资料"
            r"|前提.{0,10}(需要|要|应)?.*?纠正|先纠正|前提.{0,8}(反了|正好反)",
            answer_head,
        )
    )


def test_honest_head_recognizes_disclosure_forms():
    assert _honest_head("（笔记库里没有相关内容，以下用 AI 自身的知识回答）x")
    assert _honest_head("[NO_KB] 以下是通用解释")
    assert _honest_head("笔记里那篇只讲到 LSTM 的局限，没有涉及 RoPE，下面按通用资料推")
    assert _honest_head("库里的笔记只提到 rebase main，没涉及 --onto")
    # 2026-11-23 live 实测的真实答案头(b09/b10 形态):「笔记库暂无 X 的内容:现有…只讲到」
    assert _honest_head("笔记库暂无 overlay2 的内容：现有 Docker 笔记只讲到了 Dockerfile 结构 [1][2]")
    assert _honest_head("笔记库暂无 `worker_connections` 调优相关内容，现有 Nginx 笔记只讲了转发引擎分类")
    # false_premise 设计预期:引用库内纠错素材纠正错误前提(live f03 实测形态)
    assert _honest_head("这里有个前提需要先纠正：Docker 容器比虚拟机更「轻」，而不是更「重」[3]")
    assert _honest_head("这个前提正好反了：**容器是“轻”的那一边** [W4][W5]")


def test_honest_head_rejects_bare_citation():
    """纯背书/无归因明示 = 瞎引候选(判 WARN 交人眼)。"""
    assert not _honest_head("共享宿主机内核,只是进程级隔离 [3][5]")
    assert not _honest_head("容器的文件系统跑在镜像之上，镜像只读 [2][3]")
    assert not _honest_head("Nginx 是反向代理，worker_connections 控制并发 [1]")


def test_honest_head_accepts_false_premise_correction():
    """false_premise_in_kb 设计预期:引用库内纠错素材纠正错误前提 = PASS。
    (2026-11-23 曾被误列为反例——f02 的真实形态就是「先纠正一下:…[3][6]」)"""
    assert _honest_head("先纠正一下：BM25 根本不需要训练词向量，你笔记里已点出关键 [3][6]")
