# AI Intelligence Phase 1

Phase 1 adds an isolated AI foundation without changing purchasing transaction data.

## Included

1. Open `/AI_Intelligence` from the sidebar.
2. Use **Pricing & Margin** for deterministic pricing scenarios. Calculations run as persistent background tasks and survive page navigation.
3. Use **Settings** to add OpenRouter, Gemini, Ollama, or an OpenAI-compatible provider.
4. Test each provider before setting it as default. API keys are encrypted with Windows DPAPI and are never returned by the API.
5. `FREE_ONLY` is the default. Paid providers cannot become default until an administrator changes the operating policy.

## Hermes

Open **Settings > Hermes local service** to install Hermes with the official Nous Research Windows installer. Installation requires explicit confirmation, opens PowerShell, runs without administrator rights, and writes progress to the local dashboard data directory. After installation, use **Configure**, **Start Hermes**, and **Stop Hermes** from the same panel.

The managed local service uses `http://127.0.0.1:9119`. `HERMES_BASE_URL` remains available for a different restricted service. Health checks support `/health` and the official Hermes `/api/status` endpoint. Missing or stopped Hermes never blocks existing dashboard pages, reports, imports, or pricing calculations.

## Current Boundary

Vendor research, market crawling, item matching, AI chat, and automatic scheduling remain Phase 2 or later. No placeholder research results are generated. Pricing output is advisory and never updates orders, vendor approvals, master data, or selling prices.

## Troubleshooting

1. Open **AI Intelligence > Overview** and check worker, Hermes, and provider status.
2. Open **Research Tasks** for exact failure stage and error text.
3. Retry only failed tasks that have attempts remaining.
4. For provider errors, verify endpoint, API key, model ID, local network policy, and provider quota.

