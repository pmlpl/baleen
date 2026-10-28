import { http } from './http'

export interface HealthStatus {
  status: 'ok' | 'error'
  db: 'ok' | 'error'
  redis: 'ok' | 'error'
}

/** GET /healthz(经 Vite 代理到后端) */
export function getHealth(): Promise<HealthStatus> {
  return http.get<HealthStatus>('/healthz')
}
