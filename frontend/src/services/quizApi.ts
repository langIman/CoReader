import type {
  QuizAnswerResult,
  QuizGenerateRequest,
  QuizSSEEvent,
  QuizSession,
  QuizSessionDetail,
} from '../types/quiz'

async function asJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    let detail = fallback
    try {
      const err = await res.json()
      detail = err.detail || fallback
    } catch {
      // ignore
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function listQuizSessions(projectName: string): Promise<QuizSession[]> {
  const res = await fetch(
    `/api/quiz/sessions?project_name=${encodeURIComponent(projectName)}`,
  )
  return asJson<QuizSession[]>(res, '获取测验列表失败')
}

export async function getQuizSession(sessionId: string): Promise<QuizSessionDetail> {
  const res = await fetch(`/api/quiz/${encodeURIComponent(sessionId)}`)
  return asJson<QuizSessionDetail>(res, '获取测验详情失败')
}

export async function deleteQuizSession(sessionId: string): Promise<void> {
  const res = await fetch(`/api/quiz/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    throw new Error('删除测验失败')
  }
}

export async function submitQuizAnswer(
  sessionId: string,
  index: number,
  chosenKey: string,
): Promise<QuizAnswerResult> {
  const res = await fetch(
    `/api/quiz/${encodeURIComponent(sessionId)}/answer/${index}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chosen_key: chosenKey }),
    },
  )
  return asJson<QuizAnswerResult>(res, '提交答案失败')
}

export async function* streamGenerateQuiz(
  req: QuizGenerateRequest,
  signal?: AbortSignal,
): AsyncGenerator<QuizSSEEvent> {
  const res = await fetch('/api/quiz/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(req),
    signal,
  })
  if (!res.ok || !res.body) {
    let detail = '测验生成请求失败'
    try {
      const err = await res.json()
      detail = err.detail || detail
    } catch {
      // ignore
    }
    throw new Error(detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let sep: number
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const parsed = parseFrame(frame)
        if (parsed) yield parsed
      }
    }
    if (buffer.trim()) {
      const parsed = parseFrame(buffer)
      if (parsed) yield parsed
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // ignore
    }
  }
}

function parseFrame(frame: string): QuizSSEEvent | null {
  let eventName = ''
  const dataLines: string[] = []
  for (const raw of frame.split('\n')) {
    const line = raw.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (!eventName || dataLines.length === 0) return null
  try {
    const data = JSON.parse(dataLines.join('\n'))
    return { event: eventName, data } as QuizSSEEvent
  } catch {
    return null
  }
}
