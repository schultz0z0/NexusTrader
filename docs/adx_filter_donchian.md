# Filtro ADX na Estratégia Donchian + ZigZag

## Visão Geral
O indicador ADX (Average Directional Index) mede a **força de uma tendência**. Diferente de indicadores como RSI ou MACD, o ADX não diz se o preço vai subir ou cair, apenas se o movimento atual tem "força" (tendência forte) ou se está "fraco" (mercado lateralizado/range).

A estratégia base *Donchian + ZigZag* é uma estratégia de **reversão à média/bandas**. Ela aposta que o preço baterá nas bordas do canal Donchian e recuará. No entanto, se o mercado estiver em uma forte tendência de alta ou baixa, o preço não recua, ele rompe o canal e continua indo, o que gera operações perdedoras (Loss).

Para resolver isso, implementamos o **Filtro ADX**.

## Como funciona
A lógica original do Donchian + ZigZag permanece 100% inalterada e funciona como o motor primário de entradas. 
Quando a estratégia Donchian+ZigZag encontra um ponto de entrada (CALL ou PUT), antes de enviar a ordem para a corretora, ela aciona o filtro ADX:

1. O sistema calcula o ADX usando as últimas velas do mercado com período padrão de 14 (`ADX(14)`).
2. Compara o valor resultante com o `Filtro ADX (Máx)` configurado na dashboard.
3. Se o ADX atual for **menor ou igual** ao limite configurado, a operação é autorizada e a ordem é enviada.
4. Se o ADX atual for **maior** que o limite configurado, o robô entende que o mercado está tendencioso e perigoso para estratégias de reversão, **bloqueando a ordem** e registrando no log um aviso de bloqueio.

## Como Configurar

Na Dashboard, na seção de Estratégia, você verá um campo chamado **"Filtro ADX (Máx)"**.
O valor sugerido é `25`, que é o consenso de mercado de que acima deste valor já existe tendência de mercado, e abaixo disso é lateralização.

* **Filtro em 25**: Operará em todos os momentos que não tiver tendência forte. É o equilíbrio ideal entre volume de operações e precisão.
* **Filtro em 20**: Extremamente restrito. O mercado precisará estar muito parado/lateralizado para operar. Evita ainda mais falsos rompimentos, mas diminuirá drasticamente o número de entradas por dia.
* **Filtro em 40**: Filtro bem solto. Aceitará operar contra a tendência na maioria das vezes, a menos que o mercado esteja "rasgando" descontroladamente numa única direção.
* **Filtro em 100**: Como o ADX vai de 0 a 100, colocar 100 significa desligar o filtro na prática (todas as operações passarão).

## Requisitos Técnicos
Esta funcionalidade foi programada utilizando a biblioteca oficial `StockIndicators` (pypi: `stock-indicators`) para garantir total precisão e simetria matemática com as grandes plataformas institucionais globais. Se for hospedar um novo robô do zero, certifique-se de que o pacote `stock-indicators` consta no `requirements.txt`.
