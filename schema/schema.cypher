// =====================================================================
// Projeto GraphRAG - Dados Grafos (Dados Financeiros (Fraudes))
// Passo 1.5 - Schema do grafo: constraints e indices
// Fonte: modelo LPG do passo 1.3. Idempotente (IF NOT EXISTS).
// Aplicar com: python scripts/aplicar_schema.py
// =====================================================================

// ---------------------------------------------------------------------
// 1. CONSTRAINTS DE CHAVE (NODE KEY = valor unico + obrigatorio)
//    Cada uma cria automaticamente um indice de apoio.
// ---------------------------------------------------------------------
CREATE CONSTRAINT caso_id_chave IF NOT EXISTS
FOR (c:Caso) REQUIRE c.caso_id IS NODE KEY;

CREATE CONSTRAINT trecho_id_chave IF NOT EXISTS
FOR (t:Trecho) REQUIRE t.trecho_id IS NODE KEY;

// Reu_001, Juiz_001, Promotor_001, Agente_001: o prefixo garante que o
// codigo e unico entre todos os papeis de Pessoa.
CREATE CONSTRAINT pessoa_codigo_chave IF NOT EXISTS
FOR (p:Pessoa) REQUIRE p.codigo IS NODE KEY;

CREATE CONSTRAINT instituicao_nome_chave IF NOT EXISTS
FOR (i:InstituicaoFinanceira) REQUIRE i.nome IS NODE KEY;

CREATE CONSTRAINT empresa_nome_chave IF NOT EXISTS
FOR (e:Empresa) REQUIRE e.nome IS NODE KEY;

CREATE CONSTRAINT orgao_nome_chave IF NOT EXISTS
FOR (o:Orgao) REQUIRE o.nome IS NODE KEY;

CREATE CONSTRAINT distrito_nome_chave IF NOT EXISTS
FOR (d:Distrito) REQUIRE d.nome IS NODE KEY;

CREATE CONSTRAINT esquema_nome_chave IF NOT EXISTS
FOR (e:Esquema) REQUIRE e.nome IS NODE KEY;

CREATE CONSTRAINT crime_nome_chave IF NOT EXISTS
FOR (c:Crime) REQUIRE c.nome IS NODE KEY;

// ---------------------------------------------------------------------
// 2. CONSTRAINTS DE EXISTENCIA E DE TIPO
// ---------------------------------------------------------------------
CREATE CONSTRAINT trecho_texto_obrigatorio IF NOT EXISTS
FOR (t:Trecho) REQUIRE t.texto IS NOT NULL;

CREATE CONSTRAINT caso_titulo_obrigatorio IF NOT EXISTS
FOR (c:Caso) REQUIRE c.titulo IS NOT NULL;

CREATE CONSTRAINT caso_data_tipo IF NOT EXISTS
FOR (c:Caso) REQUIRE c.data IS :: DATE;

CREATE CONSTRAINT caso_ano_tipo IF NOT EXISTS
FOR (c:Caso) REQUIRE c.ano IS :: INTEGER;

// Procedencia: toda relacao REU_EM extraida do texto precisa da evidencia.
CREATE CONSTRAINT reu_em_evidencia_obrigatoria IF NOT EXISTS
FOR ()-[r:REU_EM]-() REQUIRE r.evidencia IS NOT NULL;

// ---------------------------------------------------------------------
// 3. INDICES B-TREE (no Neo4j 5 o tipo se chama RANGE)
//    Para filtros, ordenacao e consultas temporais.
// ---------------------------------------------------------------------
CREATE INDEX caso_data_idx IF NOT EXISTS
FOR (c:Caso) ON (c.data);

CREATE INDEX caso_ano_idx IF NOT EXISTS
FOR (c:Caso) ON (c.ano);

CREATE INDEX trecho_caso_idx IF NOT EXISTS
FOR (t:Trecho) ON (t.caso_id);

CREATE INDEX reu_em_desfecho_idx IF NOT EXISTS
FOR ()-[r:REU_EM]-() ON (r.desfecho);

CREATE INDEX envolve_instituicao_papel_idx IF NOT EXISTS
FOR ()-[r:ENVOLVE_INSTITUICAO]-() ON (r.papel);

// ---------------------------------------------------------------------
// 4. INDICES FULLTEXT (busca lexical, parte da busca hibrida)
//    Os textos do DOJ estao em ingles -> analisador 'english'.
// ---------------------------------------------------------------------
CREATE FULLTEXT INDEX trecho_texto_ft IF NOT EXISTS
FOR (t:Trecho) ON EACH [t.texto]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'english' } };

CREATE FULLTEXT INDEX caso_titulo_ft IF NOT EXISTS
FOR (c:Caso) ON EACH [c.titulo]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'english' } };

CREATE FULLTEXT INDEX organizacao_nome_ft IF NOT EXISTS
FOR (n:InstituicaoFinanceira|Empresa|Orgao) ON EACH [n.nome_exibicao];

// ---------------------------------------------------------------------
// 5. INDICES VETORIAIS (busca semantica)
//    Modelo: intfloat/multilingual-e5-base -> 768 dimensoes, cosseno.
//    Trocar de modelo exige recriar estes dois indices.
// ---------------------------------------------------------------------
CREATE VECTOR INDEX trecho_embedding_vec IF NOT EXISTS
FOR (t:Trecho) ON (t.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
} };

CREATE VECTOR INDEX caso_embedding_vec IF NOT EXISTS
FOR (c:Caso) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
} };
