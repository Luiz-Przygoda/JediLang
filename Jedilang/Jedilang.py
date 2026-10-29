import re
import json
import os
import sys
from collections import namedtuple

# Saída sempre em UTF-8 (acentos e emojis corretos no painel do VS Code / Code Runner)
sys.stdout.reconfigure(encoding="utf-8")

# --- 0. DIAGNÓSTICOS (Erros e avisos com linha e coluna) ---

Diagnostico = namedtuple("Diagnostico", ["severidade", "fase", "mensagem", "linha", "coluna"])


class ErroCompilacao(Exception):
    # Lançado pelo parser para abandonar o comando atual (recuperação em modo pânico)
    def __init__(self, fase, mensagem, linha=None, coluna=None, indice_token=None):
        super().__init__(mensagem)
        self.fase = fase
        self.mensagem = mensagem
        self.linha = linha
        self.coluna = coluna
        self.indice_token = indice_token  # posição na lista de tokens onde o parser falhou


class RelatorioDiagnosticos:
    # Acumula erros e avisos de todas as fases para a compilação não parar na primeira falha
    def __init__(self, codigo_fonte):
        self.linhas_fonte = codigo_fonte.splitlines()
        self.itens = []
        self._vistos = set()

    def _adicionar(self, severidade, fase, mensagem, linha, coluna):
        diagnostico = Diagnostico(severidade, fase, mensagem, linha, coluna)
        if diagnostico in self._vistos:  # mesma mensagem no mesmo ponto: não repete
            return
        self._vistos.add(diagnostico)
        self.itens.append(diagnostico)

    def erro(self, fase, mensagem, linha=None, coluna=None):
        self._adicionar("ERRO", fase, mensagem, linha, coluna)

    def aviso(self, fase, mensagem, linha=None, coluna=None):
        self._adicionar("AVISO", fase, mensagem, linha, coluna)

    def registrar(self, excecao):
        self.erro(excecao.fase, excecao.mensagem, excecao.linha, excecao.coluna)

    def total_erros(self):
        return sum(1 for d in self.itens if d.severidade == "ERRO")

    def total_avisos(self):
        return sum(1 for d in self.itens if d.severidade == "AVISO")

    def imprimir(self):
        print(f"\n--- Diagnósticos: {self.total_erros()} erro(s), {self.total_avisos()} aviso(s) ---")
        for d in sorted(self.itens, key=lambda d: (d.linha or 10**9, d.coluna or 0)):
            local = f"linha {d.linha}, coluna {d.coluna}" if d.linha else "fim do arquivo"
            print(f"[{d.severidade} {d.fase.upper()}] {local}: {d.mensagem}")
            # Mostra a linha do código com um '^' embaixo da coluna do problema
            if d.linha and d.linha <= len(self.linhas_fonte):
                trecho = self.linhas_fonte[d.linha - 1].replace("\t", " ")
                print(f"  {d.linha:>4} | {trecho}")
                print(f"       | {' ' * (d.coluna - 1)}^")


# --- 1. ANALISADOR LÉXICO (O Dicionário) ---

tokens = {
    "TOKEN_CHAVE": r"HOLOCRON|FORCE_FLOW|COUNCIL_ORDER|TRUST_THE_FORCE|FEEL_THE_DARK_SIDE|LIGHTSABER_DUEL|TRAINING_ROUTINE|TRANSMIT_HOLO|COMM_RECEIVE|BECOME_ONE",
    "TOKEN_TIPO_VALOR": r"KYBER_CRYSTAL|PROPHECY|BALANCE|DESTINY|LIGHT|DARK|THE_VOID",
    "TOKEN_TIPO_DADO": r"string|int|float", # --- NOVA PARTE: Tipos de dados primitivos para as funções
    "TOKEN_OP_LOGICO": r"STRONGER_THAN|WEAKER_THAN|EQUALS_THE_BALANCE|DISCORD|ALLIANCE|DIVIDED|AND|OR",
    "TOKEN_OP_MATH": r"[+\-*/]",
    "TOKEN_FLOAT_LITERAL": r"\d+\.\d+",
    "TOKEN_INT_LITERAL": r"\d+",
    "TOKEN_IDENTIFIER": r"[a-zA-Z_][a-zA-Z0-9_]*",
    "TOKEN_ASSIGN": r"=",
    "TOKEN_SIMBOLO": r"[(){};,]",
}

# --- NOVA PARTE: cada token agora carrega sua posição no código-fonte
Token = namedtuple("Token", ["tipo", "lexema", "linha", "coluna"])

# Scanner único sobre o código original (sem pré-processar), para não perder linha/coluna
PADRAO_SCANNER = re.compile(r'''
    (?P<ESPACO>\s+)
  | (?P<COMENTARIO_BLOCO>/\*.*?\*/)
  | (?P<COMENTARIO_ABERTO>/\*)
  | (?P<COMENTARIO_LINHA>//[^\n]*)
  | (?P<STRING>"[^"\n]*")
  | (?P<STRING_ABERTA>"[^"\n]*)
  | (?P<SEPARADOR>[=(){};+\-*/,])
  | (?P<PALAVRA>[^\s=(){};+\-*/,"]+)
''', re.VERBOSE | re.DOTALL)

def tokenize(codigo, diagnosticos):
    resultado = []
    erros = 0
    linha, inicio_linha = 1, 0
    lacunas = set()  # índices na lista de tokens onde algo inválido foi descartado

    for m in PADRAO_SCANNER.finditer(codigo):
        categoria, lexema = m.lastgroup, m.group()
        coluna = m.start() - inicio_linha + 1

        if categoria == "STRING":
            resultado.append(Token("TOKEN_STRING_LITERAL", lexema, linha, coluna))

        elif categoria in ("SEPARADOR", "PALAVRA"):
            tipo_encontrado = "TOKEN_DISTURBANCE_IN_THE_FORCE"
            for tipo, padrao in tokens.items():
                if re.fullmatch(padrao, lexema):
                    tipo_encontrado = tipo
                    break

            if tipo_encontrado == "TOKEN_DISTURBANCE_IN_THE_FORCE":
                # Recuperação léxica: reporta e descarta o token inválido
                erros = erros + 1
                lacunas.add(len(resultado))
                diagnosticos.erro("Léxico", f"Símbolo desconhecido '{lexema}' (perturbação na Força).", linha, coluna)
            else:
                resultado.append(Token(tipo_encontrado, lexema, linha, coluna))

        elif categoria == "STRING_ABERTA":
            erros = erros + 1
            lacunas.add(len(resultado))
            diagnosticos.erro("Léxico", "String não fechada: faltou '\"' antes do fim da linha.", linha, coluna)

        elif categoria == "COMENTARIO_ABERTO":
            erros = erros + 1
            lacunas.add(len(resultado))
            diagnosticos.erro("Léxico", "Comentário de bloco '/*' nunca é fechado com '*/'.", linha, coluna)
            break

        quebras = lexema.count("\n")
        if quebras:
            linha += quebras
            inicio_linha = m.start() + lexema.rfind("\n") + 1

    print(f"Tokens encontrados: {len(resultado)} | Erros lexicos: {erros}")
    return resultado, lacunas


# --- 2. ANALISADOR SINTÁTICO (A Gramática LL(1)) ---

class ParserJedilang:
    # Pontos de sincronização do modo pânico: o próximo comando começa em um destes
    INICIO_COMANDO = {"FORCE_FLOW", "COUNCIL_ORDER", "TRUST_THE_FORCE", "TRANSMIT_HOLO", "BECOME_ONE", "TRAINING_ROUTINE"}

    def __init__(self, tokens, diagnosticos, lacunas_lexicas=None):
        self.tokens = tokens
        self.pos = 0
        self.diagnosticos = diagnosticos
        self.lacunas_lexicas = lacunas_lexicas or set()
        if tokens:
            ultimo = tokens[-1]
            self.eof = Token("EOF", "", ultimo.linha, ultimo.coluna + len(ultimo.lexema))
        else:
            self.eof = Token("EOF", "", 1, 1)

    def token_atual(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return self.eof

    def posicao(self, token=None):
        token = token or self.token_atual()
        return {"Linha": token.linha, "Coluna": token.coluna}

    def erro(self, mensagem, linha=None, coluna=None):
        token = self.token_atual()
        raise ErroCompilacao("Sintático", mensagem, linha or token.linha, coluna or token.coluna, self.pos)

    def descrever(self, token):
        return "fim do arquivo" if token.tipo == "EOF" else token.lexema

    def consumir(self, tipo_esperado=None, lexema_esperado=None):
        token = self.token_atual()
        tipo, lexema = token.tipo, token.lexema
        if tipo_esperado and tipo != tipo_esperado:
            self.erro(f"Esperava tipo '{tipo_esperado}', encontrou '{tipo}' no lexema '{self.descrever(token)}'")
        if lexema_esperado and lexema != lexema_esperado:
            mensagem = f"Esperava '{lexema_esperado}', encontrou '{self.descrever(token)}'"
            if lexema_esperado == ";" and self.pos > 0:
                # ';' faltando: aponta para o fim do token anterior, não para a próxima linha
                anterior = self.tokens[self.pos - 1]
                self.erro(mensagem, anterior.linha, anterior.coluna + len(anterior.lexema))
            self.erro(mensagem)
        self.pos += 1
        return lexema

    def parse(self):
        return self.parse_Programa()

    def parse_Programa(self):
        corpo = self.parse_StatementList()
        # Recuperação: '}' sem '{' correspondente no nível global
        while self.token_atual().tipo != "EOF":
            token = self.token_atual()
            self.diagnosticos.erro("Sintático", "'}' inesperado (não há '{' correspondente).", token.linha, token.coluna)
            self.pos += 1
            corpo.extend(self.parse_StatementList())
        return {"TipoNodo": "Programa", "Corpo": corpo}

    def parse_StatementList(self):
        statements = []
        while self.token_atual().tipo != "EOF" and self.token_atual().lexema != "}":
            inicio = self.pos
            try:
                statements.append(self.parse_Statement())
            except ErroCompilacao as erro:
                # --- Modo pânico: registra o erro e continua no próximo comando.
                # Se o parser falhou exatamente onde o léxico descartou um token inválido,
                # o erro sintático é consequência do léxico e não é reportado (evita cascata).
                if erro.indice_token not in self.lacunas_lexicas:
                    self.diagnosticos.registrar(erro)
                self.sincronizar()
                if self.pos == inicio:
                    self.pos += 1  # garante progresso
        return statements

    def sincronizar(self):
        # Descarta tokens até ';' (consumido), '}' ou o início de um novo comando
        while self.token_atual().tipo != "EOF":
            lexema = self.token_atual().lexema
            if lexema == ";":
                self.pos += 1
                return
            if lexema == "}" or lexema in self.INICIO_COMANDO:
                return
            self.pos += 1

    def parse_Statement(self):
        tipo, lexema = self.token_atual().tipo, self.token_atual().lexema

        if lexema == "FORCE_FLOW":
            return self.parse_VarDecl()
        elif lexema == "COUNCIL_ORDER":
            return self.parse_FunctionDecl()
        elif lexema == "TRUST_THE_FORCE":
            return self.parse_IfStmt()
        elif lexema == "TRANSMIT_HOLO":
            return self.parse_FuncCall()
        elif lexema == "BECOME_ONE":
            return self.parse_ReturnStmt()
        elif lexema == "TRAINING_ROUTINE":
            return self.parse_ForStmt()
        elif tipo == "TOKEN_IDENTIFIER":
            prox_lexema = ""
            if self.pos + 1 < len(self.tokens):
                prox_lexema = self.tokens[self.pos + 1].lexema

            if prox_lexema == "(":
                ast = self.parse_UserFuncCall()
                self.consumir(lexema_esperado=";")
                return ast
            else:
                ast = self.parse_Assignment()
                self.consumir(lexema_esperado=";")
                return ast
        else:
            self.erro(f"Comando não reconhecido '{lexema}'")

    def parse_FunctionDecl(self):
        self.consumir(lexema_esperado="COUNCIL_ORDER")
        # --- NOVA PARTE: tipo de retorno opcional (ex.: COUNCIL_ORDER KYBER_CRYSTAL soma(...))
        tipo_retorno = None
        if self.token_atual().tipo == "TOKEN_TIPO_VALOR":
            tipo_retorno = self.consumir()
        pos = self.posicao()
        nome_funcao = self.consumir(tipo_esperado="TOKEN_IDENTIFIER")
        self.consumir(lexema_esperado="(")

        parametros = []
        if self.token_atual().lexema != ")":
            parametros.append(self.parse_Parametro())
            while self.token_atual().lexema == ",":
                self.consumir(lexema_esperado=",")
                parametros.append(self.parse_Parametro())

        self.consumir(lexema_esperado=")")
        bloco = self.parse_Block()
        return {"TipoNodo": "DeclaracaoFuncao", "Nome": nome_funcao, "TipoRetorno": tipo_retorno, "Parametros": parametros, "Bloco": bloco, **pos}

    def parse_Parametro(self):
        # --- CORREÇÃO AQUI: Troque "TOKEN_TIPO_DADO" por "TOKEN_TIPO_VALOR"
        tipo_dado = self.consumir(tipo_esperado="TOKEN_TIPO_VALOR")
        pos = self.posicao()
        nome_param = self.consumir(tipo_esperado="TOKEN_IDENTIFIER")
        return {"Tipo": tipo_dado, "Nome": nome_param, **pos}

    def parse_UserFuncCall(self):
        pos = self.posicao()
        nome_funcao = self.consumir(tipo_esperado="TOKEN_IDENTIFIER")
        self.consumir(lexema_esperado="(")

        argumentos = []
        if self.token_atual().lexema != ")":
            argumentos.append(self.parse_Expr())
            while self.token_atual().lexema == ",":
                self.consumir(lexema_esperado=",")
                argumentos.append(self.parse_Expr())

        self.consumir(lexema_esperado=")")
        return {"TipoNodo": "ChamadaFuncaoUsuario", "Nome": nome_funcao, "Argumentos": argumentos, **pos}

    def parse_VarDecl(self):
        self.consumir(lexema_esperado="FORCE_FLOW")
        pos = self.posicao()
        identificador = self.consumir(tipo_esperado="TOKEN_IDENTIFIER")
        self.consumir(tipo_esperado="TOKEN_ASSIGN")
        expr = self.parse_Expr()
        self.consumir(lexema_esperado=";")
        return {"TipoNodo": "DeclaracaoVariavel", "Variavel": identificador, "Valor": expr, **pos}

    def parse_Assignment(self):
        pos = self.posicao()
        identificador = self.consumir(tipo_esperado="TOKEN_IDENTIFIER")
        self.consumir(tipo_esperado="TOKEN_ASSIGN")
        expr = self.parse_Expr()
        return {"TipoNodo": "Atribuicao", "Variavel": identificador, "Valor": expr, **pos}

    def parse_ForStmt(self):
        pos = self.posicao()
        self.consumir(lexema_esperado="TRAINING_ROUTINE")
        self.consumir(lexema_esperado="(")
        inicializacao = self.parse_VarDecl()
        condicao = self.parse_Expr()
        self.consumir(lexema_esperado=";")
        atualizacao = self.parse_Assignment()
        self.consumir(lexema_esperado=")")
        bloco = self.parse_Block()
        return {"TipoNodo": "LacoFor", "Inicializacao": inicializacao, "Condicao": condicao, "Atualizacao": atualizacao, "Bloco": bloco, **pos}

    def parse_IfStmt(self):
        pos = self.posicao()
        self.consumir(lexema_esperado="TRUST_THE_FORCE")
        self.consumir(lexema_esperado="(")
        condicao = self.parse_Expr()
        self.consumir(lexema_esperado=")")
        bloco_if = self.parse_Block()
        bloco_else = None
        if self.token_atual().lexema == "FEEL_THE_DARK_SIDE":
            self.consumir(lexema_esperado="FEEL_THE_DARK_SIDE")
            bloco_else = self.parse_Block()
        return {"TipoNodo": "CondicionalIf", "Condicao": condicao, "BlocoIf": bloco_if, "BlocoElse": bloco_else, **pos}

    def parse_Block(self):
        self.consumir(lexema_esperado="{")
        statements = self.parse_StatementList()
        self.consumir(lexema_esperado="}")
        return statements

    def parse_FuncCall(self):
        pos = self.posicao()
        self.consumir(lexema_esperado="TRANSMIT_HOLO")
        self.consumir(lexema_esperado="(")
        expr = self.parse_Expr()
        self.consumir(lexema_esperado=")")
        self.consumir(lexema_esperado=";")
        return {"TipoNodo": "ChamadaFuncao", "Funcao": "TRANSMIT_HOLO", "Argumento": expr, **pos}

    def parse_ReturnStmt(self):
        pos = self.posicao()
        self.consumir(lexema_esperado="BECOME_ONE")
        expr = self.parse_Expr()
        self.consumir(lexema_esperado=";")
        return {"TipoNodo": "Retorno", "Valor": expr, **pos}

    def parse_Expr(self):
        nodo = self.parse_RelExpr()
        while self.token_atual().lexema in ["ALLIANCE", "DIVIDED", "AND", "OR"]:
            pos = self.posicao()
            op = self.consumir()
            direito = self.parse_RelExpr()
            nodo = {"TipoNodo": "OperacaoLogica", "Operador": op, "Esquerda": nodo, "Direita": direito, **pos}
        return nodo

    def parse_RelExpr(self):
        nodo = self.parse_MathExpr()
        while self.token_atual().lexema in ["STRONGER_THAN", "WEAKER_THAN", "EQUALS_THE_BALANCE", "DISCORD"]:
            pos = self.posicao()
            op = self.consumir()
            direito = self.parse_MathExpr()
            nodo = {"TipoNodo": "OperacaoRelacional", "Operador": op, "Esquerda": nodo, "Direita": direito, **pos}
        return nodo

    def parse_MathExpr(self):
        nodo = self.parse_Term()
        while self.token_atual().lexema in ["+", "-"]:
            pos = self.posicao()
            op = self.consumir()
            direito = self.parse_Term()
            nodo = {"TipoNodo": "OperacaoMatematica", "Operador": op, "Esquerda": nodo, "Direita": direito, **pos}
        return nodo

    def parse_Term(self):
        nodo = self.parse_Primary()
        while self.token_atual().lexema in ["*", "/"]:
            pos = self.posicao()
            op = self.consumir()
            direito = self.parse_Primary()
            nodo = {"TipoNodo": "OperacaoMatematica", "Operador": op, "Esquerda": nodo, "Direita": direito, **pos}
        return nodo

    def parse_Primary(self):
        token = self.token_atual()
        # --- NOVA PARTE: chamada de função dentro de expressão (ex.: FORCE_FLOW x = dobro(2);)
        if token.tipo == "TOKEN_IDENTIFIER" and self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].lexema == "(":
            return self.parse_UserFuncCall()
        if token.tipo in ["TOKEN_IDENTIFIER", "TOKEN_INT_LITERAL", "TOKEN_FLOAT_LITERAL", "TOKEN_STRING_LITERAL", "TOKEN_TIPO_VALOR"]:
            self.consumir()
            return {"TipoNodo": "LiteralOuVariavel", "Tipo": token.tipo, "Valor": token.lexema, **self.posicao(token)}
        elif token.lexema == "(":
            self.consumir(lexema_esperado="(")
            expr = self.parse_Expr()
            self.consumir(lexema_esperado=")")
            return expr
        else:
            self.erro(f"Esperava expressão, encontrou '{self.descrever(token)}'")


# --- 3. ANALISADOR SEMÂNTICO (Tabela de Símbolos + Visitor) ---

# Palavras de tipo da linguagem -> tipo interno usado pelo analisador
TIPOS_DA_LINGUAGEM = {"KYBER_CRYSTAL": "int", "BALANCE": "float", "PROPHECY": "string", "DESTINY": "bool", "THE_VOID": "void"}
NOMES_JEDI = {"int": "KYBER_CRYSTAL", "float": "BALANCE", "string": "PROPHECY", "bool": "DESTINY", "void": "THE_VOID"}
TIPOS_NUMERICOS = ("int", "float")
# "erro" é o tipo dado a expressões já com problema: ele é compatível com tudo, o que evita erros em cascata
TIPO_ERRO = "erro"

ROTULOS = {"variavel": "variável", "parametro": "parâmetro", "funcao": "função"}
OPERADORES_SIMBOLO = {
    "STRONGER_THAN": ">", "WEAKER_THAN": "<", "EQUALS_THE_BALANCE": "==", "DISCORD": "!=",
    "ALLIANCE": "and", "DIVIDED": "or", "AND": "and", "OR": "or",
}

def nome_tipo(tipo):
    if tipo in NOMES_JEDI:
        return f"{NOMES_JEDI[tipo]} ({tipo})"
    return tipo

def nome_operador(op):
    simbolo = OPERADORES_SIMBOLO.get(op)
    return f"'{op}' ({simbolo})" if simbolo else f"'{op}'"


class Simbolo:
    def __init__(self, nome, categoria, tipo, linha, coluna, parametros=None, tipo_retorno=None, implicito=False):
        self.nome = nome
        self.categoria = categoria          # "variavel", "parametro" ou "funcao"
        self.tipo = tipo                    # tipo interno: int, float, string, bool, void, erro (ou "funcao")
        self.escopo = None                  # nome do escopo onde foi declarado (preenchido pela tabela)
        self.nivel = None                   # profundidade do escopo (0 = global)
        self.linha = linha
        self.coluna = coluna
        self.parametros = parametros        # funções: lista de Simbolo dos parâmetros (None = desconhecida)
        self.tipo_retorno = tipo_retorno    # funções: None enquanto não declarado/inferido
        self.retorno_declarado = tipo_retorno is not None
        self.usado = False
        self.implicito = implicito          # criado pela recuperação de erro (nome não declarado)

    def descrever_tipo(self):
        if self.categoria != "funcao":
            return nome_tipo(self.tipo)
        if self.parametros is None:
            return "(?) -> ?"
        params = ", ".join(nome_tipo(p.tipo) for p in self.parametros)
        return f"({params}) -> {nome_tipo(self.tipo_retorno or 'void')}"


class Escopo:
    def __init__(self, nome, nivel):
        self.nome = nome
        self.nivel = nivel
        self.simbolos = {}  # tabela hash: nome -> Simbolo


class TabelaSimbolos:
    # Pilha de tabelas hash: o topo é o escopo mais interno; a busca desce até o global
    def __init__(self):
        self.pilha = []
        self.historico = []  # todos os escopos já criados, na ordem (para imprimir no final)
        self.entrar_escopo("global")

    @property
    def escopo_atual(self):
        return self.pilha[-1]

    @property
    def escopo_global(self):
        return self.pilha[0]

    def entrar_escopo(self, nome):
        escopo = Escopo(nome, len(self.pilha))
        self.pilha.append(escopo)
        self.historico.append(escopo)
        return escopo

    def sair_escopo(self):
        return self.pilha.pop()

    def declarar(self, simbolo, escopo=None):
        # Retorna o símbolo conflitante se o nome já existe NESTE escopo; senão insere e retorna None
        escopo = escopo or self.escopo_atual
        existente = escopo.simbolos.get(simbolo.nome)
        if existente and not existente.implicito:
            return existente
        simbolo.escopo, simbolo.nivel = escopo.nome, escopo.nivel
        escopo.simbolos[simbolo.nome] = simbolo
        return None

    def buscar(self, nome):
        for escopo in reversed(self.pilha):
            if nome in escopo.simbolos:
                return escopo.simbolos[nome]
        return None

    def buscar_em_escopos_externos(self, nome):
        # Usado para detectar shadowing (mesmo nome em um escopo mais externo)
        for escopo in reversed(self.pilha[:-1]):
            if nome in escopo.simbolos:
                return escopo.simbolos[nome]
        return None

    def imprimir(self):
        print("\n--- Tabela de Símbolos ---")
        for escopo in self.historico:
            recuo = "    " * escopo.nivel
            print(f"{recuo}Escopo '{escopo.nome}' (nível {escopo.nivel})")
            if not escopo.simbolos:
                print(f"{recuo}    (vazio)")
            for s in escopo.simbolos.values():
                marca = "  [implícito: criado na recuperação de erro]" if s.implicito else ""
                print(f"{recuo}    {s.nome:<16} {s.categoria:<10} {s.descrever_tipo():<45} linha {s.linha}, coluna {s.coluna}{marca}")


class VisitanteAST:
    # Padrão Visitor: cada nodo é despachado para o método visitar_<TipoNodo>
    def visitar(self, nodo):
        metodo = getattr(self, f"visitar_{nodo['TipoNodo']}", self.visitar_generico)
        return metodo(nodo)

    def visitar_generico(self, nodo):
        raise NotImplementedError(f"Nenhum visitante para o nodo '{nodo['TipoNodo']}'")


class AnalisadorSemantico(VisitanteAST):
    # Comandos retornam None; expressões retornam seu tipo e são anotadas com "TipoInferido"
    def __init__(self, diagnosticos):
        self.diagnosticos = diagnosticos
        self.tabela = TabelaSimbolos()
        self.funcoes_ativas = []  # pilha de funções sendo analisadas (para validar BECOME_ONE)

    def analisar(self, ast):
        self.visitar(ast)
        return self.tabela

    # --- Utilitários: mensagens, escopos e declarações ---

    def erro(self, mensagem, nodo):
        self.diagnosticos.erro("Semântico", mensagem, nodo.get("Linha"), nodo.get("Coluna"))

    def aviso(self, mensagem, nodo):
        self.diagnosticos.aviso("Semântico", mensagem, nodo.get("Linha"), nodo.get("Coluna"))

    def entrar_escopo(self, nome):
        self.tabela.entrar_escopo(nome)

    def sair_escopo(self):
        self.avisar_nao_usados(self.tabela.sair_escopo())

    def avisar_nao_usados(self, escopo):
        for s in escopo.simbolos.values():
            if s.categoria in ("variavel", "parametro") and not s.usado and not s.implicito:
                self.aviso(f"O valor de '{s.nome}' ({ROTULOS[s.categoria]}) nunca é lido.", {"Linha": s.linha, "Coluna": s.coluna})

    def declarar(self, simbolo):
        pos = {"Linha": simbolo.linha, "Coluna": simbolo.coluna}
        sombra = self.tabela.buscar_em_escopos_externos(simbolo.nome)
        conflito = self.tabela.declarar(simbolo)
        if conflito:
            self.erro(f"Declaração duplicada de '{simbolo.nome}': já existe {ROTULOS[conflito.categoria]} com esse nome "
                      f"no escopo '{conflito.escopo}' (linha {conflito.linha}, coluna {conflito.coluna}).", pos)
        elif sombra and not sombra.implicito:
            self.aviso(f"A declaração de '{simbolo.nome}' oculta (shadowing) {ROTULOS[sombra.categoria]} do escopo "
                       f"'{sombra.escopo}' (linha {sombra.linha}, coluna {sombra.coluna}).", pos)

    def declarar_implicito(self, nome, categoria, nodo):
        # Recuperação: registra o nome com tipo 'erro' para não repetir a mesma mensagem a cada uso
        simbolo = Simbolo(nome, categoria, TIPO_ERRO, nodo["Linha"], nodo["Coluna"], implicito=True)
        simbolo.usado = True
        escopo = self.tabela.escopo_global if categoria == "funcao" else None
        self.tabela.declarar(simbolo, escopo)

    def resolver_tipo(self, lexema, nodo, permitir_void):
        tipo = TIPOS_DA_LINGUAGEM.get(lexema)
        if tipo is None:
            self.erro(f"'{lexema}' é um valor, não um tipo. Use KYBER_CRYSTAL, BALANCE, PROPHECY ou DESTINY.", nodo)
            return TIPO_ERRO
        if tipo == "void" and not permitir_void:
            self.erro("Parâmetros não podem ser do tipo THE_VOID.", nodo)
            return TIPO_ERRO
        return tipo

    def verificar_compatibilidade(self, tipo_destino, tipo_origem, contexto, nodo_erro, nodo_anotar, chave):
        # Regras de atribuição: tipos iguais, ou coerção implícita int -> float
        if TIPO_ERRO in (tipo_destino, tipo_origem) or tipo_destino == tipo_origem:
            return True
        if tipo_destino == "float" and tipo_origem == "int":
            nodo_anotar.setdefault("Coercoes", {})[chave] = "int -> float"
            return True
        detalhe = ""
        if tipo_destino == "int" and tipo_origem == "float":
            detalhe = " (não há conversão implícita de BALANCE para KYBER_CRYSTAL: perderia a parte decimal)"
        self.erro(f"Tipos incompatíveis {contexto}: esperado {nome_tipo(tipo_destino)}, encontrado {nome_tipo(tipo_origem)}{detalhe}.", nodo_erro)
        return False

    def anotar_coercoes(self, nodo, tipo_esq, tipo_dir):
        for lado, tipo in (("Esquerda", tipo_esq), ("Direita", tipo_dir)):
            if tipo == "int":
                nodo.setdefault("Coercoes", {})[lado] = "int -> float"

    def sempre_retorna(self, comandos):
        # True se todo caminho pelos comandos termina em BECOME_ONE
        for comando in comandos:
            if comando["TipoNodo"] == "Retorno":
                return True
            if (comando["TipoNodo"] == "CondicionalIf" and comando["BlocoElse"] is not None
                    and self.sempre_retorna(comando["BlocoIf"]) and self.sempre_retorna(comando["BlocoElse"])):
                return True
        return False

    def visitar_bloco(self, comandos):
        retornou, avisado = False, False
        for comando in comandos:
            if retornou and not avisado:
                self.aviso("Código inalcançável: este comando vem depois de um BECOME_ONE.", comando)
                avisado = True  # avisa só uma vez por bloco
            self.visitar(comando)
            if self.funcoes_ativas and self.sempre_retorna([comando]):
                retornou = True

    def verificar_condicao(self, expr, comando):
        tipo = self.visitar(expr)
        if tipo not in ("bool", TIPO_ERRO):
            self.erro(f"A condição de {comando} deve ser {nome_tipo('bool')}, ou seja, LIGHT ou DARK, mas é {nome_tipo(tipo)}.", expr)

    # --- Comandos ---

    def visitar_Programa(self, nodo):
        self.visitar_bloco(nodo["Corpo"])
        self.avisar_nao_usados(self.tabela.escopo_global)

    def visitar_DeclaracaoVariavel(self, nodo):
        nome = nodo["Variavel"]
        tipo = self.visitar(nodo["Valor"])  # antes de declarar: 'FORCE_FLOW x = x;' não enxerga o novo x
        if tipo == "void":
            self.erro(f"Não é possível inferir o tipo de '{nome}': THE_VOID não é um valor utilizável.", nodo["Valor"])
            tipo = TIPO_ERRO
        self.declarar(Simbolo(nome, "variavel", tipo, nodo["Linha"], nodo["Coluna"]))

    def visitar_Atribuicao(self, nodo):
        nome = nodo["Variavel"]
        tipo_valor = self.visitar(nodo["Valor"])
        simbolo = self.tabela.buscar(nome)
        if simbolo is None:
            self.erro(f"Variável '{nome}' não declarada. Use 'FORCE_FLOW {nome} = ...;' para declará-la.", nodo)
            self.declarar_implicito(nome, "variavel", nodo)
            return
        if simbolo.categoria == "funcao":
            if not simbolo.implicito:
                self.erro(f"'{nome}' é uma função e não pode receber atribuição.", nodo)
            return
        self.verificar_compatibilidade(simbolo.tipo, tipo_valor, f"ao atribuir à variável '{nome}'", nodo["Valor"], nodo, "Valor")

    def visitar_DeclaracaoFuncao(self, nodo):
        nome = nodo["Nome"]
        if self.tabela.escopo_atual.nivel > 0:
            self.erro(f"A função '{nome}' deve ser declarada no escopo global (COUNCIL_ORDER não pode ficar dentro de blocos).", nodo)

        tipo_retorno = None
        if nodo.get("TipoRetorno"):
            tipo_retorno = self.resolver_tipo(nodo["TipoRetorno"], nodo, permitir_void=True)

        parametros = [Simbolo(p["Nome"], "parametro", self.resolver_tipo(p["Tipo"], p, permitir_void=False), p["Linha"], p["Coluna"])
                      for p in nodo["Parametros"]]
        funcao = Simbolo(nome, "funcao", "funcao", nodo["Linha"], nodo["Coluna"], parametros=parametros, tipo_retorno=tipo_retorno)
        self.declarar(funcao)  # declarada antes do corpo: permite recursão

        self.entrar_escopo(f"funcao {nome}")
        for parametro in parametros:
            self.declarar(parametro)  # parâmetros duplicados caem como declaração duplicada
        self.funcoes_ativas.append(funcao)
        self.visitar_bloco(nodo["Bloco"])
        self.funcoes_ativas.pop()
        self.sair_escopo()

        if funcao.tipo_retorno is None:
            funcao.tipo_retorno = "void"  # nenhum BECOME_ONE: função sem retorno
        elif funcao.tipo_retorno not in ("void", TIPO_ERRO) and not self.sempre_retorna(nodo["Bloco"]):
            if funcao.retorno_declarado:
                self.erro(f"A função '{nome}' declara retorno {nome_tipo(funcao.tipo_retorno)}, mas nem todos os caminhos executam BECOME_ONE.", nodo)
            else:
                self.aviso(f"Nem todos os caminhos da função '{nome}' executam BECOME_ONE.", nodo)
        nodo["Assinatura"] = funcao.descrever_tipo()

    def visitar_Retorno(self, nodo):
        tipo = self.visitar(nodo["Valor"])
        if not self.funcoes_ativas:
            self.erro("BECOME_ONE só pode ser usado dentro de uma função (COUNCIL_ORDER).", nodo)
            return
        funcao = self.funcoes_ativas[-1]
        if funcao.tipo_retorno is None:
            funcao.tipo_retorno = tipo  # sem tipo declarado: o primeiro BECOME_ONE define o retorno (inferência)
        elif not funcao.retorno_declarado and {funcao.tipo_retorno, tipo} == {"int", "float"}:
            funcao.tipo_retorno = "float"  # inferência: alarga int para float
        else:
            self.verificar_compatibilidade(funcao.tipo_retorno, tipo, f"no retorno da função '{funcao.nome}'", nodo["Valor"], nodo, "Valor")

    def visitar_CondicionalIf(self, nodo):
        self.verificar_condicao(nodo["Condicao"], "TRUST_THE_FORCE")
        self.entrar_escopo(f"bloco if (linha {nodo['Linha']})")
        self.visitar_bloco(nodo["BlocoIf"])
        self.sair_escopo()
        if nodo["BlocoElse"] is not None:
            self.entrar_escopo(f"bloco else (linha {nodo['Linha']})")
            self.visitar_bloco(nodo["BlocoElse"])
            self.sair_escopo()

    def visitar_LacoFor(self, nodo):
        # Escopo do cabeçalho (variável de controle) + escopo do corpo
        self.entrar_escopo(f"for (linha {nodo['Linha']})")
        self.visitar(nodo["Inicializacao"])
        self.verificar_condicao(nodo["Condicao"], "TRAINING_ROUTINE")
        self.visitar(nodo["Atualizacao"])
        self.entrar_escopo(f"bloco for (linha {nodo['Linha']})")
        self.visitar_bloco(nodo["Bloco"])
        self.sair_escopo()
        self.sair_escopo()

    def visitar_ChamadaFuncao(self, nodo):
        tipo = self.visitar(nodo["Argumento"])  # TRANSMIT_HOLO aceita qualquer tipo com valor
        if tipo == "void" and nodo["Argumento"]["TipoNodo"] == "ChamadaFuncaoUsuario":
            self.erro(f"A função '{nodo['Argumento']['Nome']}' não retorna valor (THE_VOID); não há o que transmitir com TRANSMIT_HOLO.", nodo["Argumento"])

    def visitar_ChamadaFuncaoUsuario(self, nodo):
        nome = nodo["Nome"]
        tipos_args = [self.visitar(arg) for arg in nodo["Argumentos"]]  # erros nos argumentos aparecem mesmo se a função não existir
        simbolo = self.tabela.buscar(nome)
        if simbolo is None:
            self.erro(f"Função '{nome}' não declarada (declare-a com COUNCIL_ORDER antes de chamá-la).", nodo)
            self.declarar_implicito(nome, "funcao", nodo)
            return TIPO_ERRO
        simbolo.usado = True
        if simbolo.categoria != "funcao":
            if not simbolo.implicito:
                self.erro(f"'{nome}' é {ROTULOS[simbolo.categoria]} do tipo {simbolo.descrever_tipo()}, não uma função; não pode ser chamado.", nodo)
            return TIPO_ERRO
        if simbolo.parametros is None:
            return TIPO_ERRO  # função implícita: o erro já foi reportado

        parametros = simbolo.parametros
        if len(tipos_args) != len(parametros):
            self.erro(f"A função '{nome}' espera {len(parametros)} argumento(s), mas recebeu {len(tipos_args)}. "
                      f"Assinatura: {nome}{simbolo.descrever_tipo()}.", nodo)
        else:
            for i, (arg, tipo_arg, param) in enumerate(zip(nodo["Argumentos"], tipos_args, parametros), start=1):
                self.verificar_compatibilidade(param.tipo, tipo_arg, f"no argumento {i} de '{nome}' (parâmetro '{param.nome}')",
                                               arg, nodo, f"Argumento {i}")
        if simbolo.tipo_retorno is None:
            # Chamada recursiva antes de qualquer BECOME_ONE: o retorno ainda não foi inferido
            self.aviso(f"O tipo de retorno de '{nome}' ainda não é conhecido nesta chamada recursiva, então ela não pode ser verificada. "
                       f"Declare-o (ex.: COUNCIL_ORDER KYBER_CRYSTAL {nome}(...)).", nodo)
        tipo = simbolo.tipo_retorno or TIPO_ERRO
        nodo["TipoInferido"] = tipo
        return tipo

    # --- Expressões ---

    def visitar_LiteralOuVariavel(self, nodo):
        tipo_token, valor = nodo["Tipo"], nodo["Valor"]
        if tipo_token == "TOKEN_INT_LITERAL":
            tipo = "int"
        elif tipo_token == "TOKEN_FLOAT_LITERAL":
            tipo = "float"
        elif tipo_token == "TOKEN_STRING_LITERAL":
            tipo = "string"
        elif tipo_token == "TOKEN_TIPO_VALOR":
            if valor in ("LIGHT", "DARK"):
                tipo = "bool"
            elif valor == "THE_VOID":
                tipo = "void"
            else:
                self.erro(f"'{valor}' é um tipo, não um valor; não pode ser usado em expressões.", nodo)
                tipo = TIPO_ERRO
        else:
            tipo = self.tipo_do_identificador(nodo)
        nodo["TipoInferido"] = tipo
        return tipo

    def tipo_do_identificador(self, nodo):
        nome = nodo["Valor"]
        simbolo = self.tabela.buscar(nome)
        if simbolo is None:
            self.erro(f"Variável '{nome}' não declarada.", nodo)
            self.declarar_implicito(nome, "variavel", nodo)
            return TIPO_ERRO
        simbolo.usado = True
        if simbolo.categoria == "funcao":
            if not simbolo.implicito:
                self.erro(f"'{nome}' é uma função e não pode ser usada como valor.", nodo)
            return TIPO_ERRO
        return simbolo.tipo

    def visitar_OperacaoMatematica(self, nodo):
        esq, dir_ = self.visitar(nodo["Esquerda"]), self.visitar(nodo["Direita"])
        op = nodo["Operador"]
        if TIPO_ERRO in (esq, dir_):
            tipo = TIPO_ERRO
        elif op == "+" and esq == dir_ == "string":
            tipo = "string"  # concatenação
        elif esq in TIPOS_NUMERICOS and dir_ in TIPOS_NUMERICOS:
            if op == "/" and self.eh_zero(nodo["Direita"]):
                self.erro("Divisão por zero.", nodo["Direita"])
            # '/' é sempre divisão real (como no interpretador); int misturado com float vira float
            tipo = "float" if op == "/" or "float" in (esq, dir_) else "int"
            if tipo == "float":
                self.anotar_coercoes(nodo, esq, dir_)
        else:
            self.erro(f"Operador '{op}' não pode ser aplicado a {nome_tipo(esq)} e {nome_tipo(dir_)}.", nodo)
            tipo = TIPO_ERRO
        nodo["TipoInferido"] = tipo
        return tipo

    def eh_zero(self, nodo):
        return (nodo["TipoNodo"] == "LiteralOuVariavel" and nodo["Tipo"] in ("TOKEN_INT_LITERAL", "TOKEN_FLOAT_LITERAL")
                and float(nodo["Valor"]) == 0)

    def visitar_OperacaoRelacional(self, nodo):
        esq, dir_ = self.visitar(nodo["Esquerda"]), self.visitar(nodo["Direita"])
        op = nodo["Operador"]
        if TIPO_ERRO not in (esq, dir_):
            numericos = esq in TIPOS_NUMERICOS and dir_ in TIPOS_NUMERICOS
            if op in ("STRONGER_THAN", "WEAKER_THAN") and not numericos:
                self.erro(f"Operador {nome_operador(op)} exige operandos numéricos, mas recebeu {nome_tipo(esq)} e {nome_tipo(dir_)}.", nodo)
            elif op in ("EQUALS_THE_BALANCE", "DISCORD") and not numericos and (esq != dir_ or esq == "void"):
                self.erro(f"Não é possível comparar {nome_tipo(esq)} com {nome_tipo(dir_)} usando {nome_operador(op)}.", nodo)
            elif numericos and esq != dir_:
                self.anotar_coercoes(nodo, esq, dir_)
        nodo["TipoInferido"] = "bool"  # o resultado é sempre bool, mesmo com erro nos operandos (evita cascata)
        return "bool"

    def visitar_OperacaoLogica(self, nodo):
        op = nodo["Operador"]
        for lado in ("Esquerda", "Direita"):
            tipo = self.visitar(nodo[lado])
            if tipo not in ("bool", TIPO_ERRO):
                self.erro(f"Operador lógico {nome_operador(op)} exige operandos {nome_tipo('bool')}; o operando da {lado.lower()} é {nome_tipo(tipo)}.", nodo[lado])
        nodo["TipoInferido"] = "bool"
        return "bool"


# --- 4. INTERPRETADOR (Para executar a AST e gerar o Holograma) ---

class InterpretadorJedilang:
    def __init__(self, ast):
        self.ast = ast
        self.memoria = {}

    def executar(self):
        print("\n---  Executando JediLang ---")
        for statement in self.ast.get("Corpo", []):
            self.executar_statement(statement)

    def executar_statement(self, nodo):
        tipo = nodo.get("TipoNodo")

        if tipo == "DeclaracaoFuncao":
            nome_func = nodo["Nome"]
            self.memoria[nome_func] = nodo

        elif tipo == "ChamadaFuncaoUsuario":
            nome_func = nodo["Nome"]

            if nome_func not in self.memoria or self.memoria[nome_func].get("TipoNodo") != "DeclaracaoFuncao":
                raise RuntimeError(f"Perturbação na força: Função '{nome_func}' não existe.")

            def_funcao = self.memoria[nome_func]
            parametros = def_funcao["Parametros"]
            argumentos = nodo["Argumentos"]

            if len(parametros) != len(argumentos):
                raise RuntimeError(f"Erro em {nome_func}: esperava {len(parametros)} argumentos, recebeu {len(argumentos)}.")

            # Avalia os argumentos passados
            valores_args = [self.avaliar_expressao(arg) for arg in argumentos]

            estado_anterior = {}
            # --- NOVA PARTE: Verificação de Tipo em Tempo de Execução e injeção do nome atualizado ---
            for param_dict, valor in zip(parametros, valores_args):
                tipo_esperado = param_dict["Tipo"]
                nome_param = param_dict["Nome"]

                # Validação estrita de tipo
                if tipo_esperado == "int" and not isinstance(valor, int):
                    raise RuntimeError(f"Erro de Tipo: Parâmetro '{nome_param}' esperava int, recebeu {type(valor).__name__}")
                elif tipo_esperado == "float" and not isinstance(valor, float):
                    # Facilita a conversão se for int
                    if isinstance(valor, int):
                        valor = float(valor)
                    else:
                        raise RuntimeError(f"Erro de Tipo: Parâmetro '{nome_param}' esperava float, recebeu {type(valor).__name__}")
                elif tipo_esperado == "string" and not isinstance(valor, str):
                    raise RuntimeError(f"Erro de Tipo: Parâmetro '{nome_param}' esperava string, recebeu {type(valor).__name__}")

                if nome_param in self.memoria:
                    estado_anterior[nome_param] = self.memoria[nome_param]
                self.memoria[nome_param] = valor
            # -----------------------------------------------------------------------------------------

            # Executa o bloco de código da função
            for stmt in def_funcao["Bloco"]:
                self.executar_statement(stmt)

            # Restaura a memória original para não vazar escopo
            for param_dict in parametros:
                nome_param = param_dict["Nome"]
                if nome_param in estado_anterior:
                    self.memoria[nome_param] = estado_anterior[nome_param]
                else:
                    del self.memoria[nome_param]

        elif tipo == "DeclaracaoVariavel" or tipo == "Atribuicao":
            nome_var = nodo["Variavel"]
            valor = self.avaliar_expressao(nodo["Valor"])
            self.memoria[nome_var] = valor

        elif tipo == "ChamadaFuncao" and nodo["Funcao"] == "TRANSMIT_HOLO":
            valor = self.avaliar_expressao(nodo["Argumento"])
            print(f"📡 [HOLOGRAMA]: {valor}")

        elif tipo == "LacoFor":
            self.executar_statement(nodo["Inicializacao"])
            while self.avaliar_expressao(nodo["Condicao"]):
                for stmt in nodo["Bloco"]:
                    self.executar_statement(stmt)
                self.executar_statement(nodo["Atualizacao"])

    def avaliar_expressao(self, nodo):
        tipo = nodo.get("TipoNodo")

        if tipo == "LiteralOuVariavel":
            val = nodo["Valor"]
            if nodo["Tipo"] == "TOKEN_IDENTIFIER":
                return self.memoria.get(val, f"Erro: Variável '{val}' não existe")
            elif nodo["Tipo"] == "TOKEN_INT_LITERAL":
                return int(val)
            elif nodo["Tipo"] == "TOKEN_FLOAT_LITERAL":
                return float(val)
            elif nodo["Tipo"] == "TOKEN_STRING_LITERAL":
                return val.replace('"', '')

        elif tipo == "OperacaoMatematica":
            esq = self.avaliar_expressao(nodo["Esquerda"])
            dir = self.avaliar_expressao(nodo["Direita"])
            op = nodo["Operador"]

            if op == "+": return esq + dir
            if op == "-": return esq - dir
            if op == "*": return esq * dir
            if op == "/": return esq / dir

        elif tipo == "OperacaoRelacional":
            esq = self.avaliar_expressao(nodo["Esquerda"])
            dir = self.avaliar_expressao(nodo["Direita"])
            op = nodo["Operador"]

            if op == "STRONGER_THAN": return esq > dir
            if op == "WEAKER_THAN": return esq < dir
            if op == "EQUALS_THE_BALANCE": return esq == dir
            if op == "DISCORD": return esq != dir

        return None


# --- 5. EXECUÇÃO PRINCIPAL ---

nome_arquivo = sys.argv[1] if len(sys.argv) > 1 else "codigo.txt"
# Se não existir a partir da pasta atual, procura na pasta do próprio Jedilang.py
if not os.path.isfile(nome_arquivo):
    caminho_alternativo = os.path.join(os.path.dirname(os.path.abspath(__file__)), nome_arquivo)
    if os.path.isfile(caminho_alternativo):
        nome_arquivo = caminho_alternativo
try:
    with open(nome_arquivo, "r", encoding="utf-8") as arquivo:
        codigo_exemplo = arquivo.read()
except FileNotFoundError:
    print(f"Erro: O arquivo '{nome_arquivo}' não foi encontrado.")
    sys.exit(1)

diagnosticos = RelatorioDiagnosticos(codigo_exemplo)

# Passo 1: Léxico
lista, lacunas_lexicas = tokenize(codigo_exemplo, diagnosticos)

# Passo 2: Sintático (com recuperação em modo pânico)
parser = ParserJedilang(lista, diagnosticos, lacunas_lexicas)
arvore_sintatica = parser.parse()

# Passo 3: Semântico — só roda sobre uma AST íntegra, para não gerar erros em cascata
tabela_simbolos = None
if diagnosticos.total_erros() == 0:
    analisador = AnalisadorSemantico(diagnosticos)
    tabela_simbolos = analisador.analisar(arvore_sintatica)
else:
    print("Análise semântica não executada: corrija primeiro os erros léxicos/sintáticos.")

diagnosticos.imprimir()

# Passo 4: Interpretador — só executa programas sem erros (avisos não bloqueiam)
if diagnosticos.total_erros() == 0:
    interpretador = InterpretadorJedilang(arvore_sintatica)
    try:
        interpretador.executar()
    except Exception as erro:
        # O programa passou na análise semântica, mas usa algo que o interpretador ainda não executa
        # (ex.: chamada de função dentro de expressão, BECOME_ONE, TRUST_THE_FORCE)
        print(f"\n[INTERPRETADOR] Execução interrompida: construção ainda não suportada na execução ({type(erro).__name__}: {erro}).")
else:
    print("\nExecução cancelada: o programa contém erros.")

# Imprime a árvore final (decorada com TipoInferido/Coercoes quando o semântico rodou)
print("\n--- Árvore Sintática (AST) Gerada ---")
print(json.dumps(arvore_sintatica, indent=4, ensure_ascii=False))

if tabela_simbolos:
    tabela_simbolos.imprimir()

sys.exit(1 if diagnosticos.total_erros() else 0)
