# Bilingual lexical retrieval fixture

This hand-written corpus contains 24 active Markdown notes and one denied decoy.
The 16 queries (eight English, eight Korean) have literal expected source paths;
the labels identify the note addressing the queried procedure or concept.
Twelve competing notes cover adjacent platform, data, continuity and team tasks.
They share vocabulary without supplying the labeled procedure.

Every query has at least four active lexical candidates. The test suite also
reverses the complete candidate ranking before taking the first three results and
checks that the unchanged default evaluator thresholds fail (recall@3 >= 0.85,
MRR@3 >= 0.75). This checks that the fixture is sensitive to broken ordering.

The corpus is small, synthetic and maintained alongside the implementation. Its
scores measure this lexical regression fixture only, not semantic retrieval,
paraphrase understanding, private-vault performance or Claude answer quality.
