import { useEffect, useRef, useState } from 'react'
import type { WsEvent } from '../types'

const WS_URL = `ws://${window.location.host}/api/stream`
const RECONNECT_MS = 3000

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

export function useEventStream(onEvent: (e: WsEvent) => void) {
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    let ws: WebSocket
    let destroyed = false
    let reconnectTimer: ReturnType<typeof setTimeout>

    function connect() {
      if (destroyed) return
      setStatus('connecting')
      ws = new WebSocket(WS_URL)

      ws.onopen = () => setStatus('connected')

      ws.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data) as WsEvent
          onEventRef.current(event)
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = () => {
        if (!destroyed) {
          setStatus('disconnected')
          reconnectTimer = setTimeout(connect, RECONNECT_MS)
        }
      }

      ws.onerror = () => ws.close()
    }

    connect()
    return () => {
      destroyed = true
      clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [])

  return status
}
