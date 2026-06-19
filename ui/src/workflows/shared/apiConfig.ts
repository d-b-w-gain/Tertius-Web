function normalizePath(pathname: string): string {
  const normalized = pathname.replace(/\/+$/g, '')
  const path = normalized.toLowerCase();

  if (path === '' || path === '/') {
    return '/api';
  }

  if (path === '/api' || path.startsWith('/api/') || path.match(/\/api\/api$/)) {
    return '/api';
  }

  return `${normalized}/api`;
}

function getFallbackApiBase() {
  return '/api';
}

function getCurrentOrigin() {
  if (typeof window === 'undefined') {
    return '';
  }
  return window.location.origin;
}

export function resolveApiBase(rawBase?: string): string {
  const configured = typeof rawBase === 'string' ? rawBase.trim() : '';
  const base = configured || getFallbackApiBase();

  if (base.startsWith('/')) {
    return normalizePath(base);
  }

  const normalizedBase = /^https?:\/\//i.test(base)
    ? base
    : `http://${base}`;

  try {
    const parsedUrl = new URL(normalizedBase);
    const normalizedPath = normalizePath(parsedUrl.pathname);
    const currentOrigin = getCurrentOrigin();
    if (!currentOrigin || parsedUrl.origin === currentOrigin) {
      return normalizedPath;
    }
    console.warn('Ignoring cross-origin VITE_API_URL; cookie-backed auth requires same-origin /api routing.');
    return getFallbackApiBase();
  } catch {
    return '/api';
  }
}

export function resolveWorkflowServerUrl(workflowBase: string, rawBase?: string): string {
  return `${resolveApiBase(rawBase)}/${workflowBase}`;
}
