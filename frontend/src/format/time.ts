const clock = new Intl.DateTimeFormat('he-IL', { hour: '2-digit', minute: '2-digit' })

/** "14:32" in the browser's time zone. The server sends ISO timestamps in UTC. */
export function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : clock.format(d)
}
