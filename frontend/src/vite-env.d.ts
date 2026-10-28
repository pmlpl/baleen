/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端基地址;缺省走 Vite 代理(/api → http://127.0.0.1:8000) */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
