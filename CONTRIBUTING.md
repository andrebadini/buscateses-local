# Contribuindo com o BuscaTeses

Obrigado pelo interesse! O BuscaTeses é feito para pesquisadores que, em geral, não programam. Por isso, clareza,
acessibilidade e privacidade pesam tanto quanto o código.

Antes de começar, leia o [Código de Conduta](CODE_OF_CONDUCT.md). O guia técnico completo está em
[docs/DESENVOLVIMENTO.md](docs/DESENVOLVIMENTO.md); a arquitetura, em [docs/ARQUITETURA.md](docs/ARQUITETURA.md).

## Formas de contribuir

- Relatar um problema com passos para reproduzir.
- Sugerir uma melhoria de uso, acessibilidade ou documentação.
- Corrigir um bug ou implementar uma melhoria.
- Melhorar testes, traduções e textos de interface.
- Revisar pull requests de outras pessoas.

Para falhas de segurança, **não** abra uma issue pública: use [SECURITY.md](SECURITY.md).

## Antes de abrir uma issue

1. Procure nas issues existentes.
2. Consulte [docs/SOLUCAO-DE-PROBLEMAS.md](docs/SOLUCAO-DE-PROBLEMAS.md).
3. Rode `buscateses status --json` e inclua o resultado (sem caminhos pessoais).
4. Use os formulários em `.github/ISSUE_TEMPLATE/`.

**Nunca** cole dados pessoais, conteúdo da sua biblioteca, tokens ou caminhos com seu nome de usuário.

## Fluxo de pull request

1. Faça um fork e crie uma branch a partir de `main` com um nome descritivo:
   `feat/filtro-por-orientador`, `fix/snippet-sem-acento`, `docs/instalacao-linux`.
2. Instale o ambiente e rode os testes:

   ```bash
   uv venv
   uv pip install -p .venv/bin/python -e ".[dev,desktop]"
   .venv/bin/python -m pytest -q -m "not slow"
   .venv/bin/ruff check src tests packaging tools
   ```

3. Faça mudanças pequenas e focadas. Inclua testes para correções e funcionalidades.
4. Atualize a documentação quando mudar comportamento visível.
5. Abra o pull request usando o modelo do repositório, explicando **o problema**, **a solução** e **como testar**.
6. Responda aos comentários da revisão. O CI precisa passar.

Pull requests que alteram a interface também devem passar no Playwright:

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/pt-br/):

```text
tipo(escopo): descrição curta no imperativo

Corpo opcional explicando o porquê.
```

Tipos comuns: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, `a11y`.
Exemplos: `feat(busca): adiciona filtro por orientador`, `fix(mcp): valida o limite de resultados`,
`docs(instalacao): explica o aviso do Gatekeeper`.

Mantenha cada commit com uma mudança coerente. Não versione dados, tokens ou arquivos gerados.

## Idioma

- **Código, identificadores, comentários e docstrings:** inglês.
- **Textos para a pessoa usuária, documentação pública e mensagens de erro:** português do Brasil, frases curtas, voz
  ativa e sem jargão sem explicação.
- **Commits e pull requests:** português ou inglês, com preferência por português.

## Regras técnicas

- Type hints e código modular; sem estado global oculto.
- O servidor escuta somente em `127.0.0.1`; o MCP é somente leitura.
- Não adicione dependências pesadas ao app (PyTorch, bancos externos).
- Preserve WCAG 2.2 AA e o funcionamento com teclado e leitor de tela.
- Não inclua caminhos absolutos da sua máquina, segredos ou dados pessoais.
- Respeite as licenças de fontes, modelo e dados (veja [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).

## Licença da contribuição

Ao enviar um pull request, você concorda que sua contribuição é licenciada sob a [licença MIT](LICENSE) do projeto.
