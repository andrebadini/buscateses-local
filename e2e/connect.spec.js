import { expect, test } from "@playwright/test";

const APPS = [
  {
    id: "cursor",
    name: "Cursor",
    kind: "auto",
    detected: true,
    connected: true,
    install_url: "https://www.cursor.com/downloads",
  },
  {
    id: "codex",
    name: "Codex CLI",
    kind: "auto",
    detected: true,
    connected: false,
    install_url: "https://github.com/openai/codex",
  },
  {
    id: "gemini_cli",
    name: "Gemini CLI",
    kind: "auto",
    detected: false,
    connected: false,
    install_url: "https://github.com/google-gemini/gemini-cli",
  },
];

const OFF = {
  state: "off",
  url: null,
  include_library: false,
  message: "Endereço público desligado.",
  cloudflared_installed: true,
};

const ONLINE = {
  state: "online",
  url: "https://teste-seguro.trycloudflare.com/mcp/segredo-de-teste-123",
  include_library: false,
  message: "Endereço público ativo.",
  cloudflared_installed: true,
};

async function mockIntegrations(page) {
  await page.route("**/api/integrations", (route) => route.fulfill({ json: APPS }));
}

test("aplicativos mostram os três estados e os assistentes na web explicam o endereço", async ({
  page,
}) => {
  await mockIntegrations(page);
  await page.route("**/api/remote", (route) => route.fulfill({ json: OFF }));

  await page.goto("/#/conectar");
  await expect(
    page.getByRole("heading", { level: 1, name: "Conectar assistentes de IA" }),
  ).toBeVisible();

  await expect(page.getByText("Conectado", { exact: true })).toBeVisible();
  await expect(page.getByText("Instalado — não conectado", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Não instalado neste computador", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Aplicativo não encontrado")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Baixar o Gemini CLI/ })).toHaveAttribute(
    "href",
    "https://github.com/google-gemini/gemini-cli",
  );

  await expect(
    page.getByRole("heading", { name: "Assistentes na web: ChatGPT e Claude.ai" }),
  ).toBeVisible();
  await expect(
    page.getByText(/qualquer pessoa que o tenha pode pesquisar no catálogo/),
  ).toBeVisible();
  await expect(page.getByRole("checkbox")).not.toBeChecked();

  await expect(
    page.getByRole("link", { name: /claude\.ai\/customize\/connectors/ }),
  ).toHaveAttribute("href", "https://claude.ai/customize/connectors");
  await expect(
    page.getByRole("link", { name: /chatgpt\.com\/plugins/ }),
  ).toHaveAttribute("href", "https://chatgpt.com/plugins");
  await expect(page.getByRole("link", { name: /Saiba mais/ })).toHaveAttribute(
    "href",
    "https://github.com/andrebadini/buscateses-local/blob/main/docs/MCP.md",
  );
  await expect(page.getByText(/docs\//)).toHaveCount(0);
});

test("criar o endereço público mostra a URL e desligar volta ao início", async ({ page }) => {
  await mockIntegrations(page);
  let current = OFF;

  await page.route("**/api/remote/start", (route) => {
    const body = route.request().postDataJSON();
    current = { ...ONLINE, include_library: Boolean(body?.include_library) };
    return route.fulfill({ json: current });
  });
  await page.route("**/api/remote/stop", (route) => {
    current = OFF;
    return route.fulfill({ json: current });
  });
  await page.route("**/api/remote/rotate", (route) => route.fulfill({ json: current }));
  await page.route("**/api/remote", (route) => route.fulfill({ json: current }));

  await page.goto("/#/conectar");
  await page.getByRole("button", { name: "Criar endereço público" }).click();

  const urlInput = page.getByLabel("Endereço do servidor MCP");
  await expect(urlInput).toHaveValue(ONLINE.url);
  await expect(page.getByRole("button", { name: "Copiar endereço" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Gerar novo endereço" })).toBeVisible();

  await page.getByRole("button", { name: "Desligar" }).click();
  await expect(page.getByRole("button", { name: "Criar endereço público" })).toBeVisible();
  await expect(urlInput).toHaveCount(0);
});

test("enquanto cria o endereço, a tela mostra o progresso", async ({ page }) => {
  await mockIntegrations(page);
  let current = OFF;
  const downloading = {
    ...OFF,
    state: "downloading",
    message: "Baixando o conector do Cloudflare (cerca de 30 MB)…",
  };
  await page.route("**/api/remote/start", (route) => {
    current = downloading;
    return route.fulfill({ json: current });
  });
  await page.route("**/api/remote", (route) => route.fulfill({ json: current }));

  await page.goto("/#/conectar");
  await page.getByRole("button", { name: "Criar endereço público" }).click();
  await expect(
    page.getByText("Baixando o conector do Cloudflare (cerca de 30 MB)…"),
  ).toBeVisible();
  await expect(page.getByRole("checkbox")).toBeDisabled();
});
