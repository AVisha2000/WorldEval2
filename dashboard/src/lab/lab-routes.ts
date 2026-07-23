export function publicGameIdFromPath(
  pathname = window.location.pathname
): string | null {
  const match = /^\/share\/games\/([a-z0-9_-]+)\/?$/.exec(pathname)
  return match ? match[1] : null
}

export function isPublicSharePath(
  pathname = window.location.pathname
): boolean {
  return /^\/share\/?$/.test(pathname)
}
