/** Map common English / HTTP errors to Chinese UI messages. */

function asText(raw: unknown): string {
  if (raw == null) return ''
  if (typeof raw === 'string') return raw.trim()
  if (Array.isArray(raw)) {
    return raw
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg?: unknown }).msg || '')
        }
        return JSON.stringify(item)
      })
      .filter(Boolean)
      .join('；')
  }
  if (typeof raw === 'object' && raw !== null && 'detail' in raw) {
    return asText((raw as { detail: unknown }).detail)
  }
  try {
    return JSON.stringify(raw)
  } catch {
    return String(raw)
  }
}

function hasChinese(s: string): boolean {
  return /[\u4e00-\u9fff]/.test(s)
}

const RULES: { re: RegExp; zh: string }[] = [
  {
    re: /^not\s*found$/i,
    zh: '未找到对应接口或资源。请确认后端已重启并加载最新代码后重试',
  },
  {
    re: /404.*not\s*found|not\s*found.*404/i,
    zh: '未找到对应接口或资源（404）。请确认后端服务正常且路由已更新',
  },
  {
    re: /yahoo.*403|403.*yahoo|finance\.yahoo\.com.*forbidden/i,
    zh: 'Yahoo 行情源暂时拒绝访问，系统会自动切换备用源；若仍失败请稍后重试',
  },
  {
    re: /无法获取 .+ 的历史行情|历史行情.*不可用|行情源暂时不可用/i,
    zh: '无法获取历史行情（行情源暂不可用）。请检查网络或稍后重试',
  },
  {
    re: /failed to fetch|networkerror|load failed|net::err_/i,
    zh: '网络请求失败，请检查后端是否已启动，或稍后重试',
  },
  {
    re: /timeout|timed?\s*out|deadline exceeded/i,
    zh: '请求超时，请稍后重试',
  },
  {
    re: /connection\s*(refused|reset|aborted)|econnrefused|econnreset/i,
    zh: '无法连接后端服务，请确认已启动并监听正确端口',
  },
  {
    re: /unauthorized|401/,
    zh: '鉴权失败或密钥无效，请检查模型 API 配置',
  },
  {
    re: /^(forbidden|access denied|没有访问权限)/i,
    zh: '没有访问权限（403）',
  },
  {
    re: /too many requests|429|rate\s*limit/i,
    zh: '请求过于频繁或触发限流，请稍后再试',
  },
  {
    re: /internal server error|500/,
    zh: '服务内部错误，请查看后端日志或稍后重试',
  },
  {
    re: /bad gateway|502/,
    zh: '上游服务异常（502），请稍后重试',
  },
  {
    re: /service unavailable|503/,
    zh: '服务暂时不可用（503），请稍后重试',
  },
  {
    re: /model_api_key|api[_ -]?key.*(missing|invalid|incorrect)|incorrect api key/i,
    zh: '模型 API Key 无效或未配置，请检查 backend/.env',
  },
  {
    re: /abort(ed)?|aborterror/i,
    zh: '请求已取消',
  },
]

const STATUS_ZH: Record<number, string> = {
  400: '请求参数有误',
  401: '未授权，请检查登录或 API 密钥',
  403: '没有访问权限',
  404: '未找到对应接口或资源。请确认后端已重启并加载最新代码后重试',
  408: '请求超时，请稍后重试',
  429: '请求过于频繁，请稍后再试',
  500: '服务内部错误，请稍后重试',
  502: '上游服务异常，请稍后重试',
  503: '服务暂时不可用，请稍后重试',
  504: '网关超时，请稍后重试',
}

/** Prefer Chinese; translate common English / HTTP failures. */
export function localizeError(raw: unknown, status?: number): string {
  const text = asText(raw)
  const lower = text.toLowerCase()

  for (const { re, zh } of RULES) {
    if (re.test(text) || re.test(lower)) return zh
  }

  if (status != null && STATUS_ZH[status]) {
    // Keep Chinese backend detail if present and more specific
    if (text && hasChinese(text) && !/^not\s*found$/i.test(text)) {
      return text
    }
    return STATUS_ZH[status]
  }

  if (text && hasChinese(text)) return text
  if (text) return `操作失败：${text}`
  if (status != null) return `请求失败（${status}）`
  return '操作失败，请稍后重试'
}
