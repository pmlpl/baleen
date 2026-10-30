/**
 * Charter 接口封装。GET /api/charter 查状态、POST /api/charter/generate 生成、PUT /api/charter 保存。
 * 问卷答案 → LLM 生成章程 → 存 DB → G2 热生效。
 */
import { http } from './http'

export interface CharterStatus {
  has_charter: boolean
  charter: string | null
}

export interface CharterAnswers {
  direction: string
  purpose: string
  collect: string[]
  reject: string[]
  unsure: string
  extra: string
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

export function saveCharter(charter: string): Promise<CharterStatus> {
  return http.put<CharterStatus>('/charter', { charter })
}
