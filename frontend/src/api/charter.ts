/**
 * Charter 接口封装。GET /api/charter 查状态、POST /api/charter/generate 生成、PUT /api/charter 保存。
 * 2026-11-26 问卷事实化:答案只有六个事实(昵称/年龄段/性别/学历/当前状态/为什么用),
 * 偏好由 LLM 从事实推导;昵称随保存落库,问答时 AI 用它称呼用户。
 */
import { http } from './http'

export interface CharterStatus {
  has_charter: boolean
  charter: string | null
  nickname: string | null
}

export interface CharterAnswers {
  nickname: string
  age_band: string
  gender: string
  education: string
  status: string
  why: string
}

export interface CharterGenerateResponse {
  charter: string
}

export function getCharter(): Promise<CharterStatus> {
  return http.get<CharterStatus>('/charter')
}

export function generateCharter(answers: CharterAnswers): Promise<CharterGenerateResponse> {
  return http.post<CharterGenerateResponse>('/charter/generate', answers)
}

export function saveCharter(charter: string, nickname?: string): Promise<CharterStatus> {
  // nickname 缺省 = 不动已有昵称;传了(含空串)才更新
  return nickname === undefined
    ? http.put<CharterStatus>('/charter', { charter })
    : http.put<CharterStatus>('/charter', { charter, nickname })
}
