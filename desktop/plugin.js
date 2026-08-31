import { Button, EmptyState, ErrorState, Input, PANES_AREA, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, host, useQuery, useQueryClient } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-computer-bridge'
const POLL_MS = 2000
const FPS = 10
// A live stream at FPS should refresh the frame version many times inside
// this window; not doing so means the pipeline stopped producing even
// though the helper process is still up.
const STALL_MS = 4000
const keys = { status: [ID, 'status'], frame: version => [ID, 'frame', version] }

/**
 * The one place that decides what the pane is allowed to claim.
 *
 * "Running" is not "live": the helper can be up with a stalled pipeline, and
 * a blank frame is a black rectangle, not a working stream. So `live` is
 * only returned when a real frame version exists, the helper counted frames,
 * the frame is not blank, and it arrived recently.
 *
 * A failed start (declining the KDE consent dialog is the common case) is a
 * STATE, not a dead end: the toolbar stays up so the user can ask again.
 * Only an unreachable backend short-circuits to ErrorState.
 */
function connectionState({ connected, failed, live, version, lastFrameAt, now }) {
  if (failed) return 'error'
  if (!connected) return 'paused'
  if (!live || !live.running) return 'connecting'
  if (!version || !live.frames) return 'connecting'
  if (live.blank) return 'blank'
  if (lastFrameAt && now - lastFrameAt > STALL_MS) return 'stalled'
  return 'live'
}

const STATE_LABEL = {
  connecting: 'Connecting…',
  live: 'Live',
  stalled: 'Stalled — no new frame',
  blank: 'Blank frame',
  paused: 'Paused',
  error: 'Stream failed'
}

/**
 * Frame renderer with per-output crop.
 *
 * The portal returns ONE bounding-box stream spanning every monitor (KDE
 * gives no per-monitor streams), so "pick a monitor" is a client-side crop
 * into the region the geometry model already knows: output.x/y/width/height.
 * `null` region = whole bounding box, letterboxed; the dead band stays
 * visible there on purpose — it is the honest picture of the stream.
 */
const KEY_MAP = {
  Enter: 'Return',
  Backspace: 'BackSpace',
  Tab: 'Tab',
  Escape: 'Escape',
  Delete: 'Delete',
  ArrowLeft: 'Left',
  ArrowUp: 'Up',
  ArrowRight: 'Right',
  ArrowDown: 'Down',
  Home: 'Home',
  End: 'End',
  PageUp: 'Page_Up',
  PageDown: 'Page_Down',
  ' ': 'space'
}

function BUTTON_NAME(index) {
  return index === 2 ? 'right' : index === 1 ? 'middle' : 'left'
}

// Modifiers we hold as real keys (down on keydown, up on keyup) so Ctrl+click
// and chords work. Shift is NOT held: the character itself already carries its
// case, which keeps ordinary typing at one request per key.
const HELD_MODS = {
  Control: 'ctrl',
  Alt: 'alt',
  AltGraph: 'alt',
  Meta: 'super'
}

function FrameCanvas({ dataUrl, region, controlling, onInput }) {
  const canvasRef = useRef(null)
  const imageRef = useRef(null)
  const placeRef = useRef(null)
  const lastMoveRef = useRef(0)

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const image = imageRef.current
    if (!canvas || !image) return
    const context = canvas.getContext('2d')
    if (!context) return
    context.imageSmoothingEnabled = true
    context.imageSmoothingQuality = 'high'
    context.clearRect(0, 0, canvas.width, canvas.height)

    let sx = 0, sy = 0, sw = image.naturalWidth, sh = image.naturalHeight
    if (region) {
      sx = Math.max(0, Math.round(region.x))
      sy = Math.max(0, Math.round(region.y))
      sw = Math.min(Math.round(region.width), image.naturalWidth - sx)
      sh = Math.min(Math.round(region.height), image.naturalHeight - sy)
      if (sw <= 0 || sh <= 0) return
    }

    const scale = Math.min(canvas.width / sw, canvas.height / sh)
    const width = Math.round(sw * scale)
    const height = Math.round(sh * scale)
    const offsetX = Math.round((canvas.width - width) / 2)
    const offsetY = Math.round((canvas.height - height) / 2)
    placeRef.current = { sx, sy, sw, sh, scale, offsetX, offsetY }
    context.drawImage(image, sx, sy, sw, sh, offsetX, offsetY, width, height)
  }, [region])

  const toStream = useCallback(event => {
    const canvas = canvasRef.current
    const place = placeRef.current
    if (!canvas || !place) return null
    const rect = canvas.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return null
    const devX = (event.clientX - rect.left) * (canvas.width / rect.width)
    const devY = (event.clientY - rect.top) * (canvas.height / rect.height)
    const x = place.sx + (devX - place.offsetX) / place.scale
    const y = place.sy + (devY - place.offsetY) / place.scale
    return {
      x: Math.round(Math.min(place.sx + place.sw, Math.max(place.sx, x))),
      y: Math.round(Math.min(place.sy + place.sh, Math.max(place.sy, y)))
    }
  }, [])

  const onMouseMove = useCallback(event => {
    if (!controlling) return
    const now = Date.now()
    if (now - lastMoveRef.current < 16) return
    lastMoveRef.current = now
    const point = toStream(event)
    if (point) onInput({ op: 'move', x: point.x, y: point.y })
  }, [controlling, onInput, toStream])

  const onMouseDown = useCallback(event => {
    if (!controlling) return
    event.preventDefault()
    const point = toStream(event)
    if (point) onInput({ op: 'move', x: point.x, y: point.y })
    onInput({ op: 'button', button: BUTTON_NAME(event.button), state: 'press' })
  }, [controlling, onInput, toStream])

  const onMouseUp = useCallback(event => {
    onInput({ op: 'button', button: BUTTON_NAME(event.button), state: 'release' })
  }, [onInput])

  const onWheel = useCallback(event => {
    if (!controlling) return
    event.preventDefault()
    onInput({ op: 'scroll', dx: event.deltaX, dy: event.deltaY })
  }, [controlling, onInput])

  useEffect(() => {
    if (!controlling) return undefined
    document.addEventListener('mouseup', onMouseUp)
    return () => document.removeEventListener('mouseup', onMouseUp)
  }, [controlling, onMouseUp])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const resize = () => {
      const box = canvas.getBoundingClientRect()
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.max(1, Math.round(box.width * ratio))
      canvas.height = Math.max(1, Math.round(box.height * ratio))
      draw()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    resize()
    return () => observer.disconnect()
  }, [draw])

  useEffect(() => {
    if (!dataUrl) return undefined
    const image = new Image()
    image.onload = () => {
      imageRef.current = image
      draw()
    }
    image.src = dataUrl
    return () => { image.onload = null }
  }, [dataUrl, draw])

  return jsx('canvas', {
    ref: canvasRef,
    'aria-label': 'Captured desktop frame',
    tabIndex: controlling ? 0 : undefined,
    onMouseMove: controlling ? onMouseMove : undefined,
    onMouseDown: controlling ? onMouseDown : undefined,
    onWheel: controlling ? onWheel : undefined,
    onContextMenu: controlling ? (event => event.preventDefault()) : undefined,
    style: {
      display: 'block',
      width: '100%',
      height: '100%',
      cursor: controlling ? 'crosshair' : 'default',
      outline: 'none'
    }
  })
}

function ConfigForm({ ctx, onSaved }) {
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [node, setNode] = useState('')
  const [vncLabel, setVncLabel] = useState('')
  const [vncHost, setVncHost] = useState('')
  const [vncPort, setVncPort] = useState('5900')
  const [vncPass, setVncPass] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    ctx.rest('/config/proxmox')
      .then(cfg => { setUrl(cfg.url || ''); setNode(cfg.node || '') })
      .catch(() => {})
  }, [ctx])

  const save = () => {
    setBusy(true)
    setError(null)
    ctx.rest('/config/proxmox', { method: 'POST', body: { url, token, node } })
      .then(() => { setBusy(false); onSaved('local') })
      .catch(err => { setBusy(false); setError(err) })
  }

  const saveVnc = () => {
    setBusy(true)
    setError(null)
    const id = (vncLabel || vncHost).trim()
    ctx.rest('/config/vnc', {
      method: 'POST',
      body: { id, label: vncLabel || vncHost, host: vncHost, port: Number(vncPort) || 5900, password: vncPass }
    })
      .then(() => { setBusy(false); onSaved('vnc:' + id) })
      .catch(err => { setBusy(false); setError(err) })
  }

  const field = (label, value, onChange, type) => jsx(Input, {
    type: type || 'text',
    value,
    placeholder: label,
    size: 'xs',
    onChange: event => onChange(event.target.value)
  })

  const heading = text => jsx('div', {
    style: { fontWeight: 600, fontSize: '13px', color: 'var(--ui-text-primary)' },
    children: text
  })

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: '10px', padding: '16px', maxWidth: '440px' },
    children: [
      heading('Connect a VNC server'),
      field('Name', vncLabel, setVncLabel),
      field('Host (or IP)', vncHost, setVncHost),
      field('Port (default 5900)', vncPort, setVncPort),
      field('Password (optional)', vncPass, setVncPass, 'password'),
      jsx(Button, { size: 'sm', disabled: busy || !vncHost, onClick: saveVnc, children: busy ? 'Saving…' : 'Add VNC' }),
      jsx('div', { style: { height: '1px', background: 'var(--ui-stroke-secondary)', margin: '6px 0' } }),
      heading('Connect a Proxmox host'),
      field('Proxmox URL (https://host:8006)', url, setUrl),
      field('API token (user@realm!id=secret)', token, setToken, 'password'),
      field('Node', node, setNode),
      error
        ? jsx('span', {
            style: { color: 'var(--ui-text-secondary)', fontSize: '12px' },
            children: String(error.message || error)
          })
        : null,
      jsx(Button, { size: 'sm', disabled: busy || !url || !node, onClick: save, children: busy ? 'Saving…' : 'Save Proxmox' })
    ]
  })
}

function TargetPicker({ targets, value, onChange }) {
  if (!targets || targets.length < 2) return null
  return jsxs(Select, {
    value,
    onValueChange: onChange,
    children: [
      jsx(SelectTrigger, {
        size: 'xs',
        style: { width: 'auto', minWidth: '120px' },
        children: jsx(SelectValue, { placeholder: 'Target' })
      }),
      jsx(SelectContent, {
        children: targets.map(t => jsx(SelectItem, { value: t.id, children: t.label }, t.id))
      })
    ]
  })
}

function OutputPicker({ outputs, value, onChange }) {
  if (!outputs || outputs.length < 2) return null
  return jsxs(Select, {
    value: value ?? outputs[0].name,
    onValueChange: onChange,
    children: [
      jsx(SelectTrigger, {
        size: 'xs',
        style: { width: 'auto', minWidth: '120px' },
        children: jsx(SelectValue, {})
      }),
      jsx(SelectContent, {
        children: outputs.map(o => jsx(SelectItem, {
          value: o.name,
          children: `${o.name} · ${o.width}x${o.height}`
        }, o.name))
      })
    ]
  })
}

function DesktopBridgePane({ ctx }) {
  const queryClient = useQueryClient()
  // `undefined` means not initialized yet; the effect below then selects the
  // first enabled output as the default monitor.
  const [outputName, setOutputName] = useState(undefined)
  const [connected, setConnected] = useState(true)
  const [startError, setStartError] = useState(null)
  const [pushedFrame, setPushedFrame] = useState(null)
  const [target, setTarget] = useState('__none__')
  const [expanded, setExpanded] = useState(false)
  const [frameSize, setFrameSize] = useState(null)
  const lastFrameRef = useRef(0)
  const lastAgentSeqRef = useRef(null)
  const overlayRef = useRef(null)
  const keyboardRef = useRef(null)
  const heldKeysRef = useRef(new Map())

  const [profile, setProfile] = useState(() => {
    try { return host?.state?.profile?.get?.() || 'default' } catch (_) { return 'default' }
  })

  useEffect(() => {
    const atom = host?.state?.profile
    if (!atom || typeof atom.listen !== 'function') return undefined
    return atom.listen(() => setProfile(atom.get() || 'default'))
  }, [])

  const targets = useQuery({
    queryKey: [ID, 'targets'],
    queryFn: () => ctx.rest('/targets'),
    staleTime: 30000
  })

  const bindings = useQuery({
    queryKey: [ID, 'binding'],
    queryFn: () => ctx.rest('/binding'),
    staleTime: 30000
  })

  useEffect(() => {
    const map = bindings.data?.bindings
    if (!map) return
    setTarget(map[profile] || '__none__')
  }, [profile, bindings.data])

  const pickTarget = useCallback(id => {
    setTarget(id)
    if (id !== '__connect__') {
      ctx.rest('/binding', { method: 'POST', body: { profile, target: id } }).catch(() => {})
    }
  }, [ctx, profile])

  const sendInput = useCallback(cmd => {
    ctx.rest('/input', { method: 'POST', body: cmd }).catch(() => {})
  }, [ctx])

  // Polling is the BASE path: ctx.socket resolves to a no-op on OAuth remotes.
  const status = useQuery({
    queryKey: keys.status,
    queryFn: () => ctx.rest('/status'),
    refetchInterval: POLL_MS
  })

  const version = status.data?.frame_version
  // Pixels are keyed by version, so the ~1.4 MB payload is fetched once per
  // frame instead of on every poll tick.
  const frame = useQuery({
    queryKey: keys.frame(version),
    queryFn: () => ctx.rest(`/frame-data?version=${encodeURIComponent(version)}`),
    enabled: Boolean(version),
    staleTime: Infinity
  })

  // Auto-start: the stream is the interaction, not a button. The cleanup is
  // what keeps a PipeWire pipeline from outliving the pane the user closed.
  useEffect(() => {
    if (!connected || target === '__connect__') return undefined
    if (target === '__none__') {
      ctx.rest('/live/stop', { method: 'POST' }).catch(() => {})
      return undefined
    }
    let dropped = false
    setStartError(null)
    ctx.rest('/live/start', {
      method: 'POST',
      body: { fps: FPS, target },
      timeoutMs: 200000
    })
      .then(() => { if (!dropped) queryClient.invalidateQueries({ queryKey: keys.status }) })
      .catch(error => { if (!dropped) setStartError(error) })
    return () => { dropped = true }
  }, [connected, ctx, queryClient, target])

  useEffect(() => () => {
    ctx.rest('/live/stop', { method: 'POST' }).catch(() => {})
  }, [ctx])

  useEffect(() => {
    if (target === 'local' || target === '__connect__') setExpanded(false)
    setFrameSize(null)
  }, [target])

  useEffect(() => {
    const src = pushedFrame?.data_url || frame.data?.data_url
    if (!src || frameSize) return undefined
    const image = new Image()
    image.onload = () => setFrameSize({ w: image.naturalWidth, h: image.naturalHeight })
    image.src = src
    return () => { image.onload = null }
  }, [pushedFrame, frame.data, frameSize])

  useEffect(() => {
    const seq = status.data?.agent_seq
    const agentTarget = status.data?.agent_target
    if (typeof seq !== 'number') return
    if (lastAgentSeqRef.current === null) {
      lastAgentSeqRef.current = seq
      return
    }
    if (seq === lastAgentSeqRef.current) return
    lastAgentSeqRef.current = seq
    if (agentTarget) setTarget(agentTarget)
  }, [status.data])

  const sendKey = useCallback((name, state) => {
    sendInput({ op: 'key', key: name, state })
  }, [sendInput])

  const releaseAll = useCallback(() => {
    heldKeysRef.current.forEach(name => sendInput({ op: 'key', key: name, state: 'release' }))
    heldKeysRef.current.clear()
  }, [sendInput])

  const onKbDown = useCallback(event => {
    if (event.key === 'Escape') {
      event.preventDefault()
      setExpanded(false)
      return
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === 'v' || event.key === 'V')) {
      event.preventDefault()
      event.stopPropagation()
      navigator.clipboard.readText()
        .then(text => ctx.rest('/clipboard', { method: 'POST', body: { text } })
          .then(() => { sendKey('v', 'press'); sendKey('v', 'release') }))
        .catch(() => {})
      return
    }
    const mod = HELD_MODS[event.key]
    if (mod) {
      event.preventDefault()
      event.stopPropagation()
      heldKeysRef.current.set(event.code, mod)
      sendKey(mod, 'press')
      return
    }
    if (event.key === 'Shift' || event.key === 'CapsLock') {
      event.preventDefault()
      return
    }
    event.preventDefault()
    event.stopPropagation()
    if (event.key.length === 1) {
      sendInput({ op: 'text', text: event.key })
      return
    }
    const named = KEY_MAP[event.key] || (/^F\d{1,2}$/.test(event.key) ? event.key : null)
    if (named) sendInput({ op: 'key', key: named })
  }, [ctx, sendKey, sendInput])

  const onKbUp = useCallback(event => {
    const mod = heldKeysRef.current.get(event.code)
    if (!mod) return
    event.preventDefault()
    event.stopPropagation()
    heldKeysRef.current.delete(event.code)
    sendKey(mod, 'release')
  }, [sendKey])

  useEffect(() => {
    if (!expanded) return undefined
    const focusKb = () => keyboardRef.current?.focus()
    focusKb()
    const raf = requestAnimationFrame(focusKb)
    const timer = setTimeout(focusKb, 60)
    const onEsc = event => { if (event.key === 'Escape') setExpanded(false) }
    const onBlur = () => releaseAll()
    window.addEventListener('keyup', onEsc)
    window.addEventListener('blur', onBlur)
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(timer)
      window.removeEventListener('keyup', onEsc)
      window.removeEventListener('blur', onBlur)
      releaseAll()
    }
  }, [expanded, releaseAll])

  // The socket only accelerates invalidation; it never becomes the only path.
  // Keying the pixel query by version means frames arriving mid-fetch simply
  // move the key forward — the stale one is dropped instead of queued.
  useEffect(() => ctx.socket('/events', message => {
    if (message?.type === 'frame' && message.data_url) {
      const receivedAt = Date.now()
      lastFrameRef.current = receivedAt
      setPushedFrame({ ...message, receivedAt })
      return
    }
    if (message && (message.type === 'ready' || message.type === 'stream')) {
      queryClient.invalidateQueries({ queryKey: keys.status })
    }
  }), [ctx, queryClient])

  useEffect(() => {
    if (version) lastFrameRef.current = Date.now()
  }, [version])

  const outputs = (status.data?.outputs || []).filter(o => o.enabled !== false)
  useEffect(() => {
    if (outputName === undefined && outputs.length > 0) {
      setOutputName(outputs[0].name)
    }
  }, [outputName, outputs])

  // Only an unreachable backend is fatal. A refused portal session is not —
  // that one keeps the toolbar so Connect can try again.
  const failure = target === '__none__' ? null : (status.error || frame.error)
  if (failure) {
    return jsx(ErrorState, {
      title: 'Desktop bridge unavailable',
      description: String(failure.message || failure)
    })
  }

  // Crop region in stream coordinates. The portal hands us ONE bounding-box
  // stream; each output's x/y/width/height inside it comes from the geometry
  // model verified against real pixels (step 2).
  const selected = outputs.find(o => o.name === outputName) || null
  const region = selected ? { x: selected.x, y: selected.y, width: selected.width, height: selected.height } : null
  const blank = status.data?.frame_blank
  const socketFresh = Boolean(pushedFrame && Date.now() - pushedFrame.receivedAt <= STALL_MS)
  const dataUrl = socketFresh
    ? pushedFrame.data_url
    : (frame.data?.data_url || pushedFrame?.data_url)
  const effectiveVersion = pushedFrame?.frame_version || version
  const state = connectionState({
    connected,
    failed: startError,
    live: status.data?.live,
    version: effectiveVersion,
    lastFrameAt: lastFrameRef.current,
    now: Date.now()
  })
  const isVm = target !== 'local' && target !== '__connect__' && target !== '__none__'

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', width: '100%', height: '100%', minHeight: 0 },
    children: [
      jsxs('div', {
        style: {
          display: 'flex', alignItems: 'center', gap: '8px',
          padding: '6px 8px', borderBottom: '1px solid var(--ui-stroke-secondary)'
        },
        children: [
          jsx(TargetPicker, {
            targets: [
              { id: '__none__', label: 'Off' },
              ...(targets.data?.targets || [{ id: 'local', label: 'Local desktop' }]),
              { id: '__connect__', label: 'Connect to new…' }
            ],
            value: target,
            onChange: pickTarget
          }),
          target === 'local'
            ? jsx(OutputPicker, { outputs, value: outputName, onChange: setOutputName })
            : null,
          target === '__none__'
            ? null
            : jsx('span', {
                style: { color: 'var(--ui-text-secondary)', fontSize: '12px' },
                children: STATE_LABEL[state]
              }),
          blank && state !== 'blank'
            ? jsx('span', {
                style: { color: 'var(--ui-text-secondary)', fontSize: '12px' },
                children: 'Blank frame'
              })
            : null
        ]
      }),
      jsx('div', {
        style: { flex: 1, minHeight: 0, overflow: 'auto' },
        children: target === '__none__'
          ? jsx('div', {
              style: {
                width: '100%', height: '100%',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'var(--ui-text-secondary)', fontSize: '13px'
              },
              children: 'No screen'
            })
          : target === '__connect__'
          ? jsx(ConfigForm, {
              ctx,
              onSaved: (next) => {
                queryClient.invalidateQueries({ queryKey: [ID, 'targets'] })
                setTarget(next || 'local')
              }
            })
          : dataUrl
            ? (isVm
                ? jsxs('div', {
                    onClick: () => setExpanded(true),
                    title: 'Click to control',
                    style: { position: 'relative', width: '100%', height: '100%', cursor: 'pointer' },
                    children: [
                      jsx(FrameCanvas, { dataUrl, region, controlling: false, onInput: sendInput }),
                      jsx('span', {
                        style: {
                          position: 'absolute', top: '6px', right: '6px',
                          padding: '2px 6px', fontSize: '11px', borderRadius: '4px',
                          background: 'var(--ui-bg-secondary)', color: 'var(--ui-text-secondary)',
                          border: '1px solid var(--ui-stroke-secondary)', pointerEvents: 'none'
                        },
                        children: 'Click to control'
                      })
                    ]
                  })
                : jsx(FrameCanvas, { dataUrl, region, controlling: false, onInput: sendInput }))
            : jsx(EmptyState, {
                title: STATE_LABEL[state],
                description: startError
                  ? String(startError.message || startError)
                  : 'Waiting for the session. KDE asks for consent once.'
              })
      }),
      expanded && isVm && dataUrl
        ? jsxs('div', {
            ref: overlayRef,
            onMouseDown: () => keyboardRef.current?.focus(),
            style: {
              position: 'fixed', inset: 0, zIndex: 9999,
              background: 'var(--ui-bg-primary)', outline: 'none',
              display: 'flex', alignItems: 'center', justifyContent: 'center'
            },
            children: [
              jsx('textarea', {
                ref: keyboardRef,
                onKeyDown: onKbDown,
                onKeyUp: onKbUp,
                autoFocus: true,
                'aria-hidden': 'true',
                style: {
                  position: 'absolute', top: 0, left: 0, width: '1px', height: '1px',
                  opacity: 0, border: 'none', padding: 0, resize: 'none',
                  pointerEvents: 'none'
                }
              }),
              jsx('div', {
                style: {
                  width: frameSize ? `${Math.round(frameSize.w * 0.5)}px` : '640px',
                  height: frameSize ? `${Math.round(frameSize.h * 0.5)}px` : '400px',
                  maxWidth: '100%', maxHeight: '100%'
                },
                children: jsx(FrameCanvas, { dataUrl, region, controlling: state === 'live', onInput: sendInput })
              }),
              jsx('div', {
                style: { position: 'absolute', top: '8px', right: '8px', zIndex: 1, display: 'flex', gap: '6px' },
                children: [
                  jsx(Button, {
                    size: 'sm',
                    onClick: () => {
                      ctx.rest('/clipboard')
                        .then(r => r && r.text && navigator.clipboard.writeText(r.text))
                        .catch(() => {})
                    },
                    children: 'Copy from VM'
                  }),
                  jsx(Button, {
                    size: 'sm',
                    onClick: () => setExpanded(false),
                    children: 'Close (Esc)'
                  })
                ]
              })
            ]
          })
        : null
    ]
  })
}

export default {
  id: ID,
  name: 'Desktop Bridge',
  description: 'Capability-first desktop capture through the Hermes gateway.',
  defaultEnabled: true,
  register(ctx) {
    const contribution = {
      id: 'viewer',
      area: PANES_AREA,
      title: 'Computer',
      data: {
        placement: 'main',
        width: '320px',
        minWidth: '200px',
        height: '320px',
        collapsible: true,
        dock: { pane: 'hermes-bots:routines', pos: 'top', enforce: true }
      },
      render: () => jsx(DesktopBridgePane, { ctx })
    }

    const $botMode = typeof host?.paneVisibility === 'function' ? host.paneVisibility('hermes-bots:pane') : null
    const $chat = host?.state?.focusedStoredSessionId ?? null
    if ($botMode && $chat && typeof $botMode.listen === 'function' && typeof $chat.listen === 'function') {
      let dispose = null
      const sync = () => {
        const active = Boolean($botMode.get()) && Boolean($chat.get())
        if (active && !dispose) {
          dispose = ctx.register(contribution)
        } else if (!active && dispose) {
          try { dispose() } catch (_) {}
          dispose = null
        }
      }
      sync()
      $botMode.listen(sync)
      $chat.listen(sync)
    } else {
      ctx.register(contribution)
    }
  }
}
