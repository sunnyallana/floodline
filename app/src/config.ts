declare const __APP_VERSION__: string;

export interface UploadConfig {
  supabaseUrl: string;
  anonKey: string;
  /** The phone's own signed token. Without one, requests carry only the anon key, which can write nothing. */
  accessToken?: () => Promise<string>;
  /** Called when the server says the token expired, so the next attempt refreshes it. */
  expireToken?: () => void;
}

/** Build-time settings. The anon key is public by design; the database only lets it insert (see supabase/). */
export const uploadConfig: UploadConfig = {
  supabaseUrl: (import.meta.env.VITE_SUPABASE_URL ?? "").replace(/\/+$/, ""),
  anonKey: import.meta.env.VITE_SUPABASE_ANON_KEY ?? "",
};

export const APP_VERSION: string = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";

export function uploadsConfigured(cfg: UploadConfig = uploadConfig): boolean {
  return cfg.supabaseUrl.startsWith("https://") && cfg.anonKey.length > 0;
}
