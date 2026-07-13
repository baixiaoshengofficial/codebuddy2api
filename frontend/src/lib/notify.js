import { reactive } from 'vue'

export const notifications = reactive([])

export function notify(message, type = 'info', duration = 4500) {
  const id = `${Date.now()}-${Math.random()}`
  notifications.push({ id, message, type })
  if (duration) window.setTimeout(() => dismiss(id), duration)
  return id
}

export function dismiss(id) {
  const index = notifications.findIndex((item) => item.id === id)
  if (index >= 0) notifications.splice(index, 1)
}
