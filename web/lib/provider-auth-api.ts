import { apiFetch, apiUrl } from "@/lib/api";

export type ProviderAuthResult = {
  provider: string;
  status: "launched" | "install_required" | "action_required";
  login_url: string;
  command: string;
  message: string;
};

export async function startProviderLogin(
  provider: string,
): Promise<ProviderAuthResult> {
  const response = await apiFetch(
    apiUrl(`/api/v1/settings/providers/${encodeURIComponent(provider)}/login`),
    { method: "POST" },
  );
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => (body as { detail?: string }).detail)
      .catch(() => "");
    throw new Error(detail || `Provider login failed (${response.status}).`);
  }
  return (await response.json()) as ProviderAuthResult;
}
