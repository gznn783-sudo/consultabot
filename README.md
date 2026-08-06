# Bot Telegram para preencher PDF exportado do Canva

O bot recebe um PDF-modelo pelo Telegram, localiza os blocos de texto que contêm marcadores, troca os valores e **refaz o bloco inteiro com novas quebras de linha**.

Isso evita o problema de escrever um nome novo por cima do nome antigo. O texto original do bloco é removido antes da inserção. Imagens, linhas, carimbos, fundos e demais partes sem marcadores não são recriados pelo código.

O projeto não adiciona rodapé, cabeçalho, marca-d'água ou aviso.

## Marcadores no Canva

O formato recomendado é simples:

```text
{{autor}}
{{cpf}}
{{processo}}
{{assunto}}
{{valor}}
{{quitacao}}
{{tribunal}}
{{advogado}}
{{nome}}
{{pix}}
{{banco}}
{{agencia}}
{{conta}}
```

O formato que você já colocou também é aceito:

```text
{{autor|95|8}}
{{processo|85|8}}
{{tribunal|150|16}}
```

Os números são ignorados nesta versão, porque a substituição ocorre no bloco completo.

Os marcadores podem ficar no meio do texto corrido:

```text
AUTORIZAÇÃO DE PROCESSO: {{processo}} Diante dos argumentos apresentados e em cumprimento à determinação emitida pelo {{tribunal}} fica estabelecido que o pagamento no valor de ({{valor}}) deve ser realizado...
```

Mantenha cada seção que precisa se reorganizar na mesma caixa de texto do Canva. Por exemplo, deixe `Autor`, `CPF`, `Assunto`, `Valor` e os marcadores no mesmo bloco quando um campo longo precisar empurrar as linhas seguintes.

Exporte como **PDF padrão** e não use a opção de achatar PDF.

## Comandos do Telegram

- `/modelo`: cadastrar ou trocar o PDF-modelo.
- `/status`: listar os marcadores encontrados.
- `/novo` ou `/gerar`: iniciar um preenchimento.
- `/cancelar`: cancelar a operação atual.

## Dados enviados ao bot

```text
autor: Jozi Biasibetti Witzorecki
cpf: 098.873.888-00
processo: 5033680-68.2026.4.04.7100
assunto: Aposentadoria por Idade - Rural (art. 48/51), Aposentadoria por Idade (Art. 48/51), Benefícios em Espécie, DIREITO PREVIDENCIÁRIO
valor: R$ 32.703,63
quitacao: R$ 1.867,50
tribunal: TRF4 · Comarca · Porto Alegre, RS
advogado: Vanessa La Cruz Bueno
nome: João Ferreira neto
pix: 88374999389
banco: neon paga-me
agencia: 0099
conta: 00937377
```

Uma linha sem `:` continua o campo anterior. Isso permite quebrar um assunto longo em várias linhas na mensagem do Telegram.

## Publicar no GitHub

Coloque estes arquivos na raiz do repositório:

```text
bot.py
pdf_engine.py
consulta.py
requirements.txt
runtime.txt
start.sh
.env.example
README.md
```

O arquivo principal precisa se chamar exatamente `bot.py`.

## Configurar no Render

Crie um **Web Service** conectado ao repositório.

- Build Command: `pip install -r requirements.txt`
- Start Command: `./start.sh`

Cadastre a variável obrigatória:

```text
BOT_TOKEN=token_fornecido_pelo_BotFather
```

O código também aceita `TELEGRAM_TOKEN` para manter compatibilidade com instalações antigas.

No Render, não é necessário cadastrar `WEBHOOK_URL`: o bot usa automaticamente a variável `RENDER_EXTERNAL_URL` fornecida pelo próprio Web Service. Para usar domínio personalizado ou executar fora do Render, cadastre:

```text
WEBHOOK_URL=https://seu-dominio.example
DATA_DIR=data
```

Não coloque `/webhook` no fim de `WEBHOOK_URL`; o código acrescenta automaticamente.

## Armazenamento

O modelo de cada usuário fica em `data/templates`. No plano sem disco persistente do Render, o arquivo pode desaparecer depois de uma reinicialização ou novo deploy. Nesse caso, envie `/modelo` novamente. Para manter os modelos, conecte um Persistent Disk e aponte `DATA_DIR` para ele.

## Funcionamento interno

1. O bot detecta o bloco do PDF que contém um ou mais marcadores.
2. Lê o texto e os estilos do bloco.
3. Substitui os marcadores na memória.
4. Remove o texto antigo do bloco.
5. Insere o bloco preenchido de novo, recalculando as linhas.
6. Se o conteúdo ultrapassar a área disponível, reduz a escala e avisa no Telegram; se ainda não couber, interrompe em vez de sobrepor outros elementos.

PDF não guarda necessariamente a caixa vazia do Canva. Por isso, faça um teste com o maior nome e o maior assunto que pretende usar. A versão atual amplia caixas curtas até a área útil da página quando não encontra uma coluna ao lado.
