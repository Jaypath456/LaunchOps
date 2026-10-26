// WebSocket client helper, the ws.js sibling to axios.js's REST client.
// Every real-time feature (course chat, live quizzes) should build on
// this rather than constructing its own WebSocket, so auth, base-URL
// derivation, and reconnect behavior stay in one place.

function getWsBaseUrl() {
  if (import.meta.env.REACT_APP_WS_URL) return import.meta.env.REACT_APP_WS_URL;

  // Derive from REACT_APP_API_URL (http(s)://host:port/api) the same
  // way axios.js falls back to a default when unset.
  const apiUrl = import.meta.env.REACT_APP_API_URL || 'http://127.0.0.1:8000/api';
  return apiUrl.replace(/^http/, 'ws').replace(/\/api\/?$/, '');
}

function getRoomWsBaseUrl(path, engine) {
  if (path.startsWith('/ws/live/') && engine === 'go' && import.meta.env.REACT_APP_LIVE_WS_URL) {
    return import.meta.env.REACT_APP_LIVE_WS_URL;
  }
  return getWsBaseUrl();
}

const MAX_RECONNECT_DELAY_MS = 30000;
const BASE_RECONNECT_DELAY_MS = 1000;

/**
 * A WebSocket wrapper that:
 *   - Sends the current access token as a WS subprotocol (matching the
 *     backend's JWTAuthMiddleware - see backend/lms_project/ws_auth.py)
 *     instead of a query string, so it never ends up in server logs.
 *   - Automatically reconnects with exponential backoff on an
 *     unexpected close, re-reading the token from sessionStorage each
 *     time in case it changed (e.g. re-login) - unless `close()` was
 *     called explicitly.
 *
 * Usage:
 *   const socket = new ReconnectingSocket('/ws/echo/', {
 *     onOpen: () => console.log('connected'),
 *     onMessage: (event) => console.log(JSON.parse(event.data)),
 *   });
 *   socket.send({ type: 'ping' });
 *   socket.close(); // stops reconnect attempts too
 */
export class ReconnectingSocket {
  constructor(path, { onOpen, onMessage, onClose, onError, engine, maxRetries = Infinity } = {}) {
    this.path = path;
    this.engine = engine;
    this.onOpen = onOpen;
    this.onMessage = onMessage;
    this.onClose = onClose;
    this.onError = onError;
    this.maxRetries = maxRetries;

    this.retryCount = 0;
    this.manuallyClosed = false;
    this.socket = null;
    this.reconnectTimer = null;

    this._connect();
  }

  _connect() {
    const token = sessionStorage.getItem('access_token');
    const url = `${getRoomWsBaseUrl(this.path, this.engine)}${this.path}`;
    const protocols = token ? [token] : [];

    this.socket = new WebSocket(url, protocols);

    this.socket.onopen = (event) => {
      this.retryCount = 0;
      if (this.onOpen) this.onOpen(event);
    };

    this.socket.onmessage = (event) => {
      if (this.onMessage) this.onMessage(event);
    };

    this.socket.onerror = (event) => {
      if (this.onError) this.onError(event);
    };

    this.socket.onclose = (event) => {
      if (this.onClose) this.onClose(event);

      if (this.manuallyClosed || this.retryCount >= this.maxRetries) {
        return;
      }

      const delay = Math.min(
        BASE_RECONNECT_DELAY_MS * 2 ** this.retryCount,
        MAX_RECONNECT_DELAY_MS
      );
      this.retryCount += 1;
      this.reconnectTimer = setTimeout(() => this._connect(), delay);
    };
  }

  send(data) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }

  close() {
    this.manuallyClosed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.socket) this.socket.close();
  }
}
