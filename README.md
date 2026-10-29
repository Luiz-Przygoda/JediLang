# JediLang

Linguagem de programação temática de Star Wars, com compilador escrito em Python, desenvolvida para a disciplina de Compiladores.

O compilador ([Jedilang/Jedilang.py](Jedilang/Jedilang.py)) passa por quatro fases:

1. **Análise léxica**: gera tokens com linha e coluna.
2. **Análise sintática**: parser LL(1) que monta a AST e se recupera de erros em modo pânico.
3. **Análise semântica**: tabela de símbolos com escopos aninhados (pilha de tabelas hash) e verificações de declarações, tipos, coerções e funções, usando o padrão Visitor.
4. **Interpretador**: executa a AST, só quando não há erros.

## Como executar

```bash
python Jedilang/Jedilang.py                                # usa Jedilang/codigo.txt
python Jedilang/Jedilang.py testes/semantico_erros.txt     # ou qualquer outro arquivo
```

O caminho é procurado a partir da pasta atual e, se não for encontrado, a partir da pasta do `Jedilang.py`.

## Exemplo

```
FORCE_FLOW aura = 2 + 2 * 4;
COUNCIL_ORDER KYBER_CRYSTAL dobro(KYBER_CRYSTAL n) {
    BECOME_ONE n * 2;
}
TRANSMIT_HOLO(aura);
```

O dicionário completo da linguagem está em [Jedilang/pergaminho_do_jedi.txt](Jedilang/pergaminho_do_jedi.txt).

## Testes

| Arquivo | O que demonstra |
|---|---|
| `Jedilang/testes/semantico_valido.txt` | Programa correto: compila e executa |
| `Jedilang/testes/semantico_valido_funcoes.txt` | `DESTINY`, recursão e chamadas em expressões |
| `Jedilang/testes/semantico_erros.txt` | Todos os erros semânticos relatados em uma única passada |
| `Jedilang/testes/sintatico_erros.txt` | Recuperação de erros sintáticos e léxicos |
