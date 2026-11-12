# Clipping Diário — Prefeitura de Anápolis

Faz sozinho a rotina do clipping: lê os portais locais, separa o que cita as palavras-chave, **abre cada matéria, puxa os primeiros parágrafos e a data**, e monta o documento no mesmo formato que você já usa:

```
[Portal Contexto] Nova arma contra mosquito da dengue, pontos para Wolbitos estão mapeados.
16/09/2026
A Prefeitura de Anápolis concluiu o mapeamento de mais de cinco mil pontos...
Com essa etapa finalizada, o município avança nos trabalhos preparatórios...
https://portalcontexto.com/nova-arma/
```

Gera três arquivos ao mesmo tempo na pasta `clippings/`: **.docx** (para enviar), **.html** (Ctrl+P → salvar como PDF) e **.txt** (para colar no e-mail ou WhatsApp).

## Instalação (uma vez só)

```bash
pip install -r requirements.txt
```

Python 3.9+. Sem chave de API.

## Uso diário

```bash
python clipping.py
python clipping.py --horas 72          # segunda-feira, cobrindo o fim de semana
python clipping.py --paragrafos 2      # resumos mais curtos
```

## Configuração (`config.json`)

- **`fontes`** — os portais monitorados. Adicionar um novo é só incluir o RSS:
  ```json
  {"nome": "Nome do Portal", "rss": "https://site.com.br/feed/"}
  ```
  A maioria dos portais é WordPress, então quase sempre o feed é o endereço do site + `/feed/`.
  `"sempre": true` (Prefeitura e Câmara) entra no clipping sem precisar bater palavra-chave, já que tudo ali é da gestão.
- **`palavras_chave`** — suas 55 palavras. Incluir ou tirar termo aqui, sem mexer no código.
- **`paragrafos`** — quantos parágrafos de cada matéria entram (padrão 3).
- **`ruido`** — trechos que nunca devem entrar no texto ("Leia também", "Publicidade", "Compartilhe"...). Se aparecer alguma sujeira nova de algum portal, acrescente aqui.
- **`usar_google_news`** — além dos portais da lista, varre o Google Notícias por cada palavra-chave, para pegar veículo de fora que falou de Anápolis. Deixe `false` se quiser rodar mais rápido.
- **`horas`** — janela padrão de busca.

## Como ele decide o que entra

1. Lê o RSS de cada portal e descarta o que é antigo demais.
2. Se o título ou o resumo cita uma palavra-chave (ou é da Prefeitura/Câmara), abre a matéria.
3. Na página, tira menu, rodapé, scripts e publicidade; pega o título real (`og:title`), a data de publicação e os primeiros parágrafos de verdade — ignorando frases curtas, legendas e chamadas tipo "Leia também".
4. Confere a palavra-chave também no corpo do texto, porque muita matéria só cita a Prefeitura no meio.
5. Tira repetidas pelo endereço da matéria e ordena da mais recente para a mais antiga.

## Rodar sozinho todo dia

**Windows** — Agendador de Tarefas → tarefa básica → diária → "Iniciar um programa":
Programa `python`, argumentos `clipping.py`, iniciar em: a pasta do projeto.

**Linux/Mac** — `crontab -e`:
```
0 7 * * * cd /caminho/do/projeto && /usr/bin/python3 clipping.py
```

## Fontes prioritárias

Portal 6, Portal Contexto, Diário Município, Prefeitura e Câmara estão marcadas como `"prioritaria": true` no `config.json`. No final da execução, o script mostra um alerta bem visível se alguma delas não trouxer nenhuma matéria naquele dia:

```
!!! ATENÇÃO - fonte(s) prioritária(s) sem nenhuma matéria hoje:
    - Portal 6: Connection refused
    Confira esses sites manualmente antes de enviar o clipping.
```

Isso pode significar duas coisas: o feed caiu (erro de conexão) ou o feed funcionou mas nenhuma matéria do dia bateu com as palavras-chave — o que também vale a pena conferir manualmente, pois pode ser sinal de mudança no formato do RSS do site. Para marcar outra fonte como prioritária, é só acrescentar `"prioritaria": true` nela no `config.json`.

## CMTT e Polícia Civil

- **CMTT** não precisou de fonte separada: as notícias da Companhia Municipal de Trânsito e Transporte ficam dentro do próprio site da Prefeitura (`anapolis.go.gov.br`), então já entram pelo feed da Prefeitura.
- **Polícia Civil de Goiás** foi adicionada como fonte (`goias.gov.br/policiacivil`). Como é um site estadual — cobre o estado inteiro, não só Anápolis —, ela **não** está marcada como `sempre`: só entra no clipping quando o título ou o resumo da matéria cita "Anápolis". Eu não consegui confirmar o endereço exato do RSS a partir daqui (o ambiente onde eu testo não acessa a internet real); se ao rodar aparecer `! feed indisponivel` para essa fonte, me avisa o erro que eu acho o endereço certo.

## Janela de tempo

Por padrão, o clipping busca notícias **das 11:30 do dia anterior até o momento em que você roda o script** — não é mais um bloco fixo de 24h. Para mudar o horário de corte:

```bash
python clipping.py --hora-corte 08:00
```

Ou edite `"hora_corte": "11:30"` no `config.json`. Para uma janela diferente (ex.: segunda-feira cobrindo o fim de semana), use `--horas`, que ignora o corte de 11:30 e conta um número fixo de horas para trás a partir de agora:

```bash
python clipping.py --horas 72
```

## Notícias repetidas em portais diferentes

Quando a mesma notícia sai em mais de um portal (comum quando reproduzem a mesma nota oficial da Prefeitura), o script identifica pelo título e pelo primeiro parágrafo, escolhe a versão com mais conteúdo como principal e lista as outras só com portal + link, sem repetir o texto:

```
[Portal Contexto] Nova arma contra mosquito da dengue...
17/09/2026
A Prefeitura de Anápolis concluiu o mapeamento...
https://portalcontexto.com/...
[Portal 6] https://portal6.com.br/...
```

Essa comparação funciona bem quando os portais reproduzem a mesma nota (caso mais comum). Se dois portais escreverem sobre o mesmo fato com texto totalmente diferente um do outro, pode não detectar — nesse caso as duas entram como matérias separadas mesmo.

## Toda matéria tem que ter conteúdo

Nenhuma matéria entra no clipping só com o link. Se o script não conseguir extrair o texto da página (site bloqueou, layout diferente), ele usa o resumo do RSS do próprio portal como conteúdo. Se nem isso existir, a matéria é descartada, e aparece no terminal `! descartei` — melhor faltar do que aparecer sem texto.

## Relevância para o prefeito

O filtro de relevância hoje é por palavra-chave: uma matéria só entra se o título, o resumo do RSS ou o corpo do texto citar algum dos termos configurados (Prefeitura, secretarias, o prefeito, a Câmara etc.) — ou, para os portais locais cadastrados, se citar "Anápolis" de forma geral. Isso corta a maior parte do irrelevante automaticamente.

O que a palavra-chave **não** resolve sozinha é julgar "importância" — se uma nota é digna da atenção do prefeito ou é um detalhe menor. Isso é avaliação editorial, e eu não incluí nenhum filtro automático de "importância" porque errar nisso é pior do que trazer uma matéria a mais: cortar automaticamente algo que parecia pequeno mas era relevante é o tipo de erro que não dá pra perceber depois. Prefiro que o script traga tudo que bate com as palavras-chave e você decida rápido, folheando, o que vai pro prefeito ou não — é mais rápido que reler a internet toda, mas ainda precisa do seu olho.

Se quiser reduzir ainda mais o volume, dá pra apertar o filtro de dois jeitos, os dois configuráveis sem mexer no código:
- Tirar do `config.json` alguma palavra-chave genérica demais que estiver trazendo muita coisa de pouca relevância.
- Desligar o `"usar_google_news": true` do config, ficando só com os portais locais cadastrados — reduz bastante o volume, ao custo de perder cobertura de veículo grande de fora que raramente fala de Anápolis.

## Quando falta o texto de uma matéria

Se o site bloqueia o acesso automático ou usa uma estrutura de página que o script não reconhece, ele agora **cai para o resumo que já vem no RSS do portal**, em vez de deixar a matéria sem texto. No terminal aparece um aviso assim:

```
+ Nome da matéria  [usei o resumo do RSS: pagina bloqueou o acesso (403 Forbidden)]
```

Se nem o RSS trouxer resumo, a matéria é descartada (aparece `! descartei`) — melhor não entrar no clipping do que entrar só com link.

## Limitações conhecidas

- Portal sem RSS ou com paywall não é lido; nesses casos o jeito é continuar no olho.
- Cada portal monta o HTML do seu jeito. Se algum aviso "usei o resumo do RSS" aparecer com frequência para o mesmo site, me manda o link de uma matéria dele que eu ajusto o extrator para reconhecer aquele layout.
- Links do Google Notícias são de redirecionamento — o script tenta descobrir o endereço original, mas nem sempre o Google deixa.
