# Acessibilidade

O BuscaTeses tem como meta o nível **WCAG 2.2 AA**. A interface é usada por pesquisadores com diferentes níveis de
experiência e precisa funcionar para quem usa teclado, leitor de tela, zoom, alto contraste ou Libras.

## Recursos implementados

- **Estrutura semântica:** `header`, `nav`, `main`, `footer` e títulos hierárquicos; link "Pular para o conteúdo" no
  início da página.
- **Navegação por teclado:** todos os controles são alcançáveis e acionáveis pelo teclado; a ordem de foco segue a
  ordem visual.
- **Foco visível:** contorno claro em links, botões, campos e elementos com `tabindex`; o título principal recebe foco
  ao trocar de página para leitores de tela anunciarem o novo conteúdo.
- **Formulários:** rótulos associados, mensagens de erro descritivas e agrupamento de filtros.
- **Anúncios dinâmicos:** resultados, avisos e progresso usam regiões `aria-live` para que o leitor de tela saiba o que
  mudou.
- **Diálogos acessíveis:** foco preso no diálogo, fechamento com `Esc` e retorno do foco ao elemento de origem.
- **Contraste:** paleta com contraste suficiente nos temas claro e escuro; suporte a `forced-colors` (alto contraste do
  sistema).
- **Movimento:** animações são reduzidas ou desligadas quando o sistema pede `prefers-reduced-motion`.
- **Alvos de toque:** tamanho mínimo de 24 × 24 px para controles, conforme a WCAG 2.2.
- **Zoom:** layout responsivo, sem perda de conteúdo com zoom de 200%.
- **VLibras (opcional, desligado por padrão):** o tradutor de Libras só é carregado depois que a pessoa ativa o botão
  **Libras** no cabeçalho ou a opção em **Configurações → Acessibilidade**. A escolha fica lembrada no navegador; se o
  serviço externo falhar, o restante da interface continua funcionando. Com a opção desligada, nenhum conteúdo da página
  é enviado ao `vlibras.gov.br`.
- **Tema:** claro, escuro ou automático (segue o sistema).

## Testes automatizados

O projeto usa **Playwright** com **axe-core**. O teste percorre todas as rotas principais nos temas claro e escuro e
falha se houver violação de impacto `serious` ou `critical` nas regras WCAG 2.0, 2.1 e 2.2 níveis A e AA.

Rotas verificadas: busca, resultados, ficha da tese, biblioteca, seleção, conectar IA, configurações, sobre e
primeiros passos.

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

O CI roda a suíte em um servidor de teste com dados sintéticos. Os testes automatizados não substituem a avaliação
manual: eles não detectam tudo (por exemplo, qualidade da leitura em voz alta ou sentido do conteúdo).

## Roteiro manual de teste

Faça este roteiro antes de uma release e ao alterar a interface. Registre o sistema, o navegador e a versão do
BuscaTeses.

### 1. Somente teclado

1. Abra o app e pressione `Tab` repetidamente desde o início. Confirme que o primeiro foco é o link "Pular para o
   conteúdo".
2. Percorra o menu, os filtros, os cartões de resultado, a paginação e os botões de ação sem usar o mouse.
3. Confirme que o foco é sempre visível e que não fica preso em nenhum lugar.
4. Abra um diálogo (por exemplo, exportar ou remover) e feche com `Esc`; confirme que o foco volta ao botão de origem.
5. Use `Enter` e `Espaço` para acionar botões e links. Use as setas quando houver grupos de opções.

### 2. Leitor de tela

- macOS: **VoiceOver** (`Cmd + F5`).
- Windows: **NVDA** (gratuito) ou **Narrador**.
- Linux: **Orca**.

Verifique:

1. O título da página e o título principal são anunciados ao navegar.
2. O link "Pular para o conteúdo" leva ao conteúdo principal.
3. Os campos de busca e filtros têm nome, tipo e estado anunciados.
4. Os resultados são anunciados após a busca (região `aria-live`), sem leitura duplicada.
5. Os trechos destacados fazem sentido no texto lido (o destaque não depende apenas de cor).
6. Mensagens de erro e progresso são anunciadas.

### 3. Zoom de 200%

1. Aumente o zoom do navegador para 200% (ou use a lupa do sistema).
2. Confirme que não há rolagem horizontal, sobreposição de texto nem conteúdo cortado.
3. Repita nas telas de busca, ficha, biblioteca e configurações.

### 4. Alto contraste

1. Ative o modo de alto contraste do sistema (Windows: **Configurações → Acessibilidade → Contraste**; macOS:
   **Acessibilidade → Tela → Aumentar contraste**).
2. Confirme que textos, bordas, foco e ícones continuam visíveis.
3. Teste também o tema escuro do app.

### 5. VLibras (opcional, desligado por padrão)

1. Abra o app com a opção desligada e confirme que **nenhum** widget do VLibras é carregado e que nenhuma requisição é
   feita a `vlibras.gov.br`.
2. Ative o botão **Libras** no cabeçalho (ou **Configurações → Acessibilidade**) e confirme que o widget aparece.
3. Abra o widget e traduza um texto curto.
4. Desligue a internet e recarregue: a interface deve continuar funcionando, mesmo sem o widget.
5. Desative a opção e confirme que o widget é removido e que a escolha continua valendo ao recarregar.

### 6. Movimento e toque

1. Ative "reduzir movimento" no sistema e confirme que as animações diminuem.
2. Em um celular ou com emulação de toque, confirme que os alvos têm pelo menos 24 px e que o menu abre e fecha.

## Limitações conhecidas

- O VLibras é opcional, vem desligado por padrão, depende de um serviço externo e da conexão com a internet; sem ele, a
  tradução em Libras não está disponível.
- PDFs digitalizados e imagens dentro de documentos não têm descrição automática.
- A qualidade dos trechos destacados depende do texto extraído; PDFs com layout complexo podem ter ordem de leitura
  estranha.
- Testes automatizados cobrem violações detectáveis por regras; avaliação com pessoas usuárias é recomendada para
  mudanças grandes.

## Como reportar uma barreira

Abra uma issue em <https://github.com/andrebadini/buscateses-local/issues> com:

- a tela e o passo a passo para reproduzir;
- o sistema, o navegador e a versão do BuscaTeses;
- o leitor de tela, o zoom ou o modo de contraste usados;
- o que você esperava e o que aconteceu.

Se preferir não expor detalhes publicamente, use o canal privado descrito em [SECURITY.md](../SECURITY.md).
Contribuições com correções são bem-vindas; veja [CONTRIBUTING.md](../CONTRIBUTING.md).
