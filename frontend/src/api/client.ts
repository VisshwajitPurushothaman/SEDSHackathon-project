/** Ground-station API base URL (the backend on this laptop). */
export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000';
