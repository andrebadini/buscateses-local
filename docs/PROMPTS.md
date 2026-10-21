# Pedidos prontos para assistentes de IA

Copie, ajuste o que estiver entre colchetes e cole no seu assistente. Os pedidos de instalação servem para assistentes
que executam comandos no seu computador (Claude Code, Claude Desktop com acesso ao computador, Codex, Cursor, Gemini CLI
e similares). Os pedidos de pesquisa funcionam em qualquer assistente conectado ao BuscaTeses por MCP.

> Ao falar com um assistente, use o nome do comando de linha de comando do seu sistema: `buscateses-cli` no macOS e no
> Windows (instaladores) ou `buscateses` no Linux e nas instalações por código-fonte. Veja a tabela do
> [AGENTS.md](../AGENTS.md).

## Instalação

### Instalar e deixar tudo pronto

```text
Instale e configure o BuscaTeses (https://github.com/andrebadini/buscateses-local) neste computador,
seguindo as instruções do arquivo AGENTS.md do repositório. Quero:
1. o aplicativo instalado pela versão mais recente publicada em Releases (use o instalador do meu sistema);
2. o modelo de embedding e o catálogo de teses baixados e prontos (comando "buscateses setup --yes");
3. o servidor MCP do BuscaTeses conectado ao assistente que eu uso: [Claude Desktop, Claude Code, Codex, Cursor,
   Gemini CLI ou LM Studio];
4. uma verificação final com "buscateses status" e uma busca de teste.
Explique cada passo em linguagem simples e peça minha confirmação antes de instalar qualquer programa.
```

### Instalar pelo código-fonte (sem instalador)

```text
Instale o BuscaTeses a partir do código-fonte (https://github.com/andrebadini/buscateses-local), conforme a seção
"Alternativa: a partir do código-fonte" do AGENTS.md, usando o uv. Depois rode "buscateses setup --yes", conecte o
MCP ao [nome do assistente] com "buscateses connect" e verifique com "buscateses status". Peça confirmação antes de
instalar o uv ou qualquer outro programa.
```

### Só conectar o MCP (o app já está instalado)

```text
O BuscaTeses já está instalado neste computador. Encontre o executável de linha de comando (veja a tabela do AGENTS.md
em https://github.com/andrebadini/buscateses-local), rode "buscateses connect --list" e conecte-o ao
[nome do assistente]. Depois me diga se preciso reiniciar algum programa.
```

### Diagnosticar um problema

```text
O BuscaTeses não está funcionando como esperado: [descreva o que acontece]. Rode "buscateses status --json", leia o
final do arquivo de log (logs/buscateses.log na pasta de dados do BuscaTeses) e consulte
docs/SOLUCAO-DE-PROBLEMAS.md no repositório https://github.com/andrebadini/buscateses-local. Explique a causa e
proponha a correção antes de alterar qualquer coisa.
```

### Usar no ChatGPT ou no Claude.ai (endereço público)

```text
O BuscaTeses já está instalado neste computador e quero usá-lo no [ChatGPT / Claude.ai]. Na tela "Conectar
assistentes", seção "Assistentes na web", crie o endereço público com a biblioteca pessoal desmarcada, copie a URL e
me mostre o passo a passo para adicionar esse endereço como conector no [ChatGPT / Claude.ai]. Avise que o endereço
funciona só enquanto o BuscaTeses estiver aberto e que qualquer pessoa com ele pode pesquisar no catálogo. Quando eu
avisar que terminei, desligue o endereço.
```

Se preferir pelo terminal, o comando é `buscateses remote start` (mantenha-o rodando) e `buscateses remote stop` para
desligar. Não use `--include-library` sem que a pessoa peça: a opção expõe trechos dos documentos pessoais.

## Pesquisa (com o MCP conectado)

### Levantamento bibliográfico

```text
Use o BuscaTeses para fazer um levantamento de teses e dissertações sobre [tema], defendidas entre [ano] e [ano]
[opcional: em programas de (área) / no estado (UF)]. Faça várias buscas com termos diferentes e sinônimos, reúna os
20 trabalhos mais relevantes e apresente uma tabela com título, autor, ano, nível, instituição e um resumo de uma
linha. Inclua o link de cada trabalho quando houver.
```

### Mapear o estado da arte

```text
Com o BuscaTeses, identifique as principais linhas de abordagem em teses e dissertações brasileiras sobre [tema].
Agrupe os trabalhos por abordagem, indique as instituições e os orientadores que mais aparecem e aponte lacunas que
parecem pouco exploradas. Cite cada trabalho por autor e ano.
```

### Encontrar trabalhos parecidos com um que eu já conheço

```text
Encontre no BuscaTeses a tese "[título]" de [autor] e use a ferramenta de trabalhos semelhantes para listar os 10
mais próximos. Explique em uma frase o que cada um tem em comum com o original.
```

### Pesquisar na minha biblioteca

```text
Pesquise na minha biblioteca do BuscaTeses o que os meus livros dizem sobre [conceito]. Traga os trechos mais
relevantes com o título do livro e a página, e depois escreva uma síntese de um parágrafo citando essas páginas.
```

### Cruzar a biblioteca com o catálogo

```text
Use o BuscaTeses para: (1) buscar na minha biblioteca o conceito de [conceito] e resumir como os autores o definem;
(2) buscar no catálogo teses e dissertações que aplicaram esse conceito a [contexto]. Apresente as duas partes
separadas e cite as fontes (livro e página; autor, ano e instituição).
```

> Dica: assistentes podem errar. Confira títulos, autores e anos diretamente no BuscaTeses antes de citar.
