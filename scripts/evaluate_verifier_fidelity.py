#!/usr/bin/env python3
"""Mutation fidelity of the literal preservation check on synthetic notes (stdlib only).

    python scripts/evaluate_verifier_fidelity.py
    python scripts/evaluate_verifier_fidelity.py --json
    python scripts/evaluate_verifier_fidelity.py --control always_complete

Every bundled note first has to pass check_preservation unchanged. Each mutation
of a note is then run through check_preservation and through the preserve-check
path (propose, then preserve_check) in a temporary vault that is removed
afterwards. A candidate that propose refuses is counted as blocked, not as a
detection by the literal check. The result is counted per mutation category,
with categories the documentation puts out of scope in separate rows and a
one-sided 95% Clopper-Pearson upper bound from scripts/eval_stats.py next to
every rate.

needs_review with the only reason no_selected_literal_facts means the check had
nothing to measure. Such a note never enters the table, and on a mutant that
outcome counts as unmeasured, never as a catch.

No model, network or user vault is involved. The notes and mutations are written
by hand for this file, so a rate here describes this file and nothing else: it
is not evidence of real-world safety. See docs/verifier-fidelity.md.
Exit code 0 when the table was produced, 2 for usage or fixture errors, which
are reported as content-free codes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
EVAL_STATS_PATH = ROOT / 'scripts' / 'eval_stats.py'
CONFIDENCE = 0.95
CONTROLS = ('always_complete', 'always_review')
AUTHORITY = 'synthetic_measurement_not_safety_evidence'
TARGET = '20-knowledge/note.md'
EXEMPT_TARGET = 'CLAUDE.md'
CANDIDATE = '00-meta/candidate.md'
NO_FACTS = 'no_selected_literal_facts'
CONFIG = {'required_keys': ['title', 'type', 'status', 'created', 'updated'],
          'enums': {'type': ['concept', 'guide'], 'status': ['active', 'draft']}}

# (category, scope, why it is not in scope). The reasons name the limit the
# documentation states for check_preservation; see docs/lesson-proposals.md.
CATEGORIES = (
    ('value_change', 'in_scope', ''),
    ('value_change_behind_broken_fence', 'in_scope', ''),
    ('value_change_unextracted', 'out_of_scope',
     'only number, date, label and wiki tokens are counted; words, model codes and qualifiers are not'),
    ('unit_change_listed', 'in_scope', ''),
    ('unit_change_unlisted', 'out_of_scope', 'a suffix outside the unit list falls off the token'),
    ('swap', 'out_of_scope', 'occurrences are counted, positions are not compared'),
    ('negation_insertion', 'out_of_scope', 'a sentence rewritten with the same literals is caught only when declared'),
    ('fact_free_paragraph_deletion', 'out_of_scope', 'text without a literal fact is not counted'),
    ('tail_truncation_with_facts', 'in_scope', ''),
    ('tail_truncation_fact_free', 'out_of_scope', 'text without a literal fact is not counted'),
    ('frontmatter_destruction', 'out_of_scope', 'bodies are compared without frontmatter'),
    ('frontmatter_destruction_exempt_target', 'out_of_scope',
     'bodies are compared without frontmatter and this target has no schema check'),
    ('rewrite_literals_kept', 'false_alarm', 'meaning kept; any flag is a false alarm'),
    ('rewrite_literals_reformatted', 'false_alarm', 'meaning kept; literal formats differ, documented as possible'),
)


class FidelityError(Exception):
    """A content-free error code."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: ARG002 - never echo the rejected value
        raise FidelityError('invalid_arguments')


def F(text):  # noqa: N802 - paragraph with at least one literal fact
    return (text, True)


def T(text):  # noqa: N802 - paragraph without a literal fact
    return (text, False)


# Hand-written fixtures. The fact markers are set by reading the text, not by
# the extraction rules under test. Replacements hold (old, new) or
# (old, new, claim); a claim is the phrase a careful author would declare.
NOTES = (
    {'id': 'ko-retention', 'language': 'ko', 'title': '접속 기록 보존 규칙', 'date': '2026-03-02', 'wiki': '기록 보관 정책',
     'paragraphs': (T('# 접속 기록 보존 규칙'),
                    F('접속 기록은 2026-03-02부터 90일 보존하고 월간 한도는 15건이다.'),
                    F('담당자: 홍길동'),
                    T('보존 절차는 모든 팀에 같은 방식으로 적용한다.'),
                    F('보관 위치는 [[기록 보관 정책]] 문서를 따른다.'),
                    F('긴급 복구는 4시간 안에 끝내고 백업 12개를 유지한다. 대기 인원은 세 명이고 장비 모델은 AB1234이다.'),
                    T('감사 전에는 관리자가 백업을 확인한다.'),
                    T('예외는 보안 담당이 별도로 승인한다.')),
     'values': (('2026-03-02', '2026-04-02'), ('90일', '30일'), ('15건', '16건'), ('홍길동', '김철수'),
                ('기록 보관 정책', '기록 폐기 정책'), ('12개', '14개')),
     'unextracted': (('세 명', '다섯 명', '대기 인원은 세 명이고'), ('AB1234', 'AB1235', '장비 모델은 AB1234이다')),
     'units_listed': (('4시간', '4분'), ('12개', '12대'), ('15건', '15명')),
     'units_unlisted': (('90일', '90주', '90일 보존하고'), ('90일', '90년', '90일 보존하고')),
     'swaps': (('90일', '4시간', '90일 보존하고'),),
     'negations': (('백업을 확인한다.', '백업을 확인하지 않는다.', '백업을 확인한다.'),
                   ('별도로 승인한다.', '별도로 승인하지 않는다.', '별도로 승인한다.')),
     'kept': (('접속 기록은 2026-03-02부터 90일 보존하고', '2026-03-02부터 접속 기록을 90일 보관하고'),
              ('보존 절차는 모든 팀에 같은 방식으로 적용한다.', '모든 팀은 보존 절차를 같은 방식으로 따른다.')),
     'reformatted': (('15건', '15 건'), ('2026-03-02', '2026년 3월 2일'), ('담당자: 홍길동', '담당은 홍길동이 맡는다'))},
    {'id': 'ko-panel', 'language': 'ko', 'title': '배전반 점검 기록', 'date': '2026-05-11', 'wiki': '설비 점검 표',
     'paragraphs': (T('# 배전반 점검 기록'),
                    F('시험 전압은 22kV이고 정격 용량은 630kVA이다.'),
                    F('최근 점검일은 2026-05-11이고 다음 점검은 2026-11-11이다.'),
                    F('담당자: 이영희'),
                    T('점검 결과는 현장 일지에 남긴다.'),
                    F('체결 기록은 [[설비 점검 표]] 문서에 있고 부품 무게는 5kg이다.'),
                    F('간격은 10mm이고 정격 전류는 630A이다. 허용 온도는 60도 이상이고 펌웨어는 v2.5이다.'),
                    T('이상이 보이면 작업을 멈춘다.'),
                    T('교대 전에 책임자가 결과를 검토한다.')),
     'values': (('22kV', '23kV'), ('630kVA', '640kVA'), ('2026-05-11', '2026-05-12'), ('이영희', '박민수'),
                ('설비 점검 표', '설비 교체 표'), ('10mm', '12mm')),
     'unextracted': (('60도 이상이고', '60도 이하이고', '60도 이상이고'), ('v2.5', 'v3.5', '펌웨어는 v2.5이다')),
     'units_listed': (('22kV', '22MV'), ('630kVA', '630MVA'), ('5kg', '5g'), ('10mm', '10m')),
     'units_unlisted': (('630A', '630V', '정격 전류는 630A이다'),),
     'swaps': (('2026-05-11', '2026-11-11', '최근 점검일은 2026-05-11이고'),),
     'negations': (('작업을 멈춘다.', '작업을 멈추지 않는다.', '작업을 멈춘다.'),
                   ('결과를 검토한다.', '결과를 검토하지 않는다.', '결과를 검토한다.')),
     'kept': (('점검 결과는 현장 일지에 남긴다.', '현장 일지에 점검 결과를 남긴다.'),
              ('시험 전압은 22kV이고', '22kV가 시험 전압이고')),
     'reformatted': (('22kV', '22 kV'), ('5kg', '5KG'), ('630kVA', '630 kVA'))},
    {'id': 'ko-budget', 'language': 'ko', 'title': '장비 예산 메모', 'date': '2026-07-01', 'wiki': '구매 절차',
     'paragraphs': (T('# 장비 예산 메모'),
                    F('올해 예산은 350만원이고 잔액은 125,000원이다.'),
                    F('구매 수량은 12대이고 승인 인원은 3명이다.'),
                    T('지출 내역은 분기마다 공유한다.'),
                    F('이름: 김철수'),
                    F('견적 비교는 [[구매 절차]] 문서를 따른다.'),
                    F('보증은 3년이고 유지보수는 2년이다. 증가율은 1.5%이며 회의는 2026-07-01 오전에 열린다. 수수료는 5천 원이다.'),
                    T('구매 전에는 재무 담당이 승인한다.'),
                    T('미사용 예산은 다음 분기로 넘긴다.')),
     'values': (('350만원', '300만원'), ('125,000원', '125,500원'), ('12대', '13대'), ('김철수', '이영희'),
                ('구매 절차', '폐기 절차'), ('1.5%', '2.5%')),
     'unextracted': (('오전에 열린다', '오후에 열린다', '오전에 열린다'), ('5천 원', '5만 원', '수수료는 5천 원이다')),
     'units_listed': (('12대', '12명'), ('3명', '3건'), ('350만원', '350원'), ('125,000원', '125,000건')),
     'units_unlisted': (('3년', '3일', '보증은 3년이고'), ('2년', '2주', '유지보수는 2년이다')),
     'swaps': (('3년', '2년', '보증은 3년이고'),),
     'negations': (('재무 담당이 승인한다.', '재무 담당이 승인하지 않는다.', '재무 담당이 승인한다.'),
                   ('다음 분기로 넘긴다.', '다음 분기로 넘기지 않는다.', '다음 분기로 넘긴다.')),
     'kept': (('지출 내역은 분기마다 공유한다.', '분기마다 지출 내역을 공유한다.'),
              ('구매 수량은 12대이고', '12대를 구매 수량으로 잡고')),
     'reformatted': (('350만원', '350만 원'), ('1.5%', '1.5퍼센트'), ('12대', '12 대'))},
    {'id': 'en-retention', 'language': 'en', 'title': 'Access log retention', 'date': '2026-03-02',
     'wiki': 'Records Policy',
     'paragraphs': (T('# Access log retention'),
                    F('Access logs are kept for 90 days from 2026-03-02 and the monthly limit is 15 items.'),
                    F('Name: Atlas'),
                    T('The procedure applies to every team in the same way.'),
                    F('Storage follows [[Records Policy]] without exception.'),
                    F('Recovery finishes within 4 hours and 12 copies stay online. Three engineers stand by and the model is AB1234.'),
                    T('An administrator must verify the backup before each audit.'),
                    T('The security lead may approve an exception.')),
     'values': (('90 days', '30 days'), ('2026-03-02', '2026-04-02'), ('15 items', '16 items'), ('Atlas', 'Borealis'),
                ('Records Policy', 'Archive Policy'), ('12 copies', '14 copies')),
     'unextracted': (('Three engineers', 'Five engineers', 'Three engineers stand by'),
                     ('AB1234', 'AB1235', 'the model is AB1234')),
     'units_listed': (),
     'units_unlisted': (('90 days', '90 weeks', 'kept for 90 days'), ('4 hours', '4 days', 'within 4 hours')),
     'swaps': (('90 days', '4 hours', 'kept for 90 days'),),
     'negations': (('must verify', 'must not verify', 'must verify the backup'),
                   ('may approve', 'may not approve', 'may approve an exception')),
     'kept': (('The procedure applies to every team in the same way.', 'Every team follows the procedure alike.'),
              ('Recovery finishes within 4 hours', 'Within 4 hours recovery finishes')),
     'reformatted': (('2026-03-02', 'March 2, 2026'), ('Name: Atlas', 'Atlas is the name on record'),
                     ('15 items', 'fifteen items'))},
    {'id': 'en-panel', 'language': 'en', 'title': 'Switchgear inspection record', 'date': '2026-05-11',
     'wiki': 'Inspection Table',
     'paragraphs': (T('# Switchgear inspection record'),
                    F('The test voltage is 22kV and the rated capacity is 630kVA.'),
                    F('The last check was 2026-05-11 and the next check is 2026-11-11.'),
                    F('Person: Borealis'),
                    T('Results go into the site journal.'),
                    F('Torque records live in [[Inspection Table]] and the part weighs 5kg.'),
                    F('The gap is 10mm and the rated current is 630A. The limit is at least 60 degrees and the firmware is v2.5.'),
                    T('Stop the work when a fault appears.'),
                    T('The shift lead reviews the result before handover.')),
     'values': (('22kV', '23kV'), ('630kVA', '640kVA'), ('2026-05-11', '2026-05-12'), ('Borealis', 'Cobalt'),
                ('Inspection Table', 'Replacement Table'), ('10mm', '12mm')),
     'unextracted': (('at least 60', 'at most 60', 'at least 60 degrees'), ('v2.5', 'v3.5', 'firmware is v2.5')),
     'units_listed': (('22kV', '22MV'), ('5kg', '5g'), ('10mm', '10m')),
     'units_unlisted': (('630A', '630V', 'rated current is 630A'),),
     'swaps': (('2026-05-11', '2026-11-11', 'last check was 2026-05-11'),),
     'negations': (('Stop the work when', 'Do not stop the work when', 'Stop the work when a fault appears.'),
                   ('reviews the result', 'does not review the result', 'reviews the result before handover')),
     'kept': (('Results go into the site journal.', 'The site journal holds the results.'),
              ('The test voltage is 22kV', 'The voltage under test is 22kV')),
     'reformatted': (('22kV', '22 kV'), ('5kg', '5 kg'), ('630kVA', '630 kVA'))},
    {'id': 'en-budget', 'language': 'en', 'title': 'Equipment budget memo', 'date': '2026-07-01',
     'wiki': 'Purchase Procedure',
     'paragraphs': (T('# Equipment budget memo'),
                    F('The budget is 350,000 USD and the balance is 125,000 USD.'),
                    F('We buy 12 units and 3 people approve.'),
                    T('Spending is shared every quarter.'),
                    F('Company: Cobalt'),
                    F('Quotes follow [[Purchase Procedure]] at all times.'),
                    F('The warranty runs 3 years and support runs 2 years. Growth is 1.5% and the review meets '
                      'on 2026-07-01 in the morning, within ±5 days of the invoice.'),
                    T('Finance must approve each purchase.'),
                    T('Unused budget rolls into the next quarter.')),
     'values': (('350,000', '300,000'), ('125,000', '125,500'), ('12 units', '13 units'), ('Cobalt', 'Atlas'),
                ('Purchase Procedure', 'Disposal Procedure'), ('1.5%', '2.5%')),
     'unextracted': (('in the morning', 'in the evening', 'in the morning'), ('±5', '5', 'within ±5 days')),
     'units_listed': (),
     'units_unlisted': (('3 years', '3 days', 'runs 3 years'), ('2 years', '2 weeks', 'runs 2 years')),
     'swaps': (('3 years', '2 years', 'warranty runs 3 years'),),
     'negations': (('must approve', 'must not approve', 'must approve each purchase'),
                   ('rolls into', 'does not roll into', 'rolls into the next quarter')),
     'kept': (('Spending is shared every quarter.', 'Every quarter the spending is shared.'),
              ('We buy 12 units', 'The purchase is 12 units')),
     'reformatted': (('350,000 USD', '350000 USD'), ('1.5%', '1.50%'), ('12 units', 'twelve units'))},
    {'id': 'ko-no-facts', 'language': 'ko', 'title': '작성 원칙', 'date': '2026-02-02', 'wiki': '',
     'paragraphs': (T('# 작성 원칙'), T('문장은 짧게 쓴다.'), T('어려운 말은 풀어 쓴다.'), T('같은 말을 반복하지 않는다.'))},
    {'id': 'en-no-facts', 'language': 'en', 'title': 'Writing principles', 'date': '2026-02-02', 'wiki': '',
     'paragraphs': (T('# Writing principles'), T('Keep each sentence short.'), T('Explain hard words plainly.'),
                    T('Do not repeat yourself.'))},
)


def _frontmatter(note, *, closing='---'):
    related = 'related: ["[[%s]]"]\n' % note['wiki'] if note['wiki'] else ''
    return ('---\ntitle: %s\ntype: concept\nstatus: active\ncreated: %s\nupdated: %s\n%s%s\n'
            % (note['title'], note['date'], note['date'], related, closing))


def _join(paragraphs):
    return '\n\n'.join(text for text, _ in paragraphs) + '\n'


def render(note):
    return _frontmatter(note) + _join(note['paragraphs'])


def _swap(body, first, second):
    if first not in body or second not in body:
        raise FidelityError('fixture_literal_missing')
    marker = '\x00'
    return body.replace(first, marker, 1).replace(second, first, 1).replace(marker, second, 1)


def _replace(body, old, new):
    if old not in body:
        raise FidelityError('fixture_literal_missing')
    return body.replace(old, new, 1)


def _edits(note, field):
    """(old, new, claim) rows of one replacement field."""
    return [(*row, *(() if len(row) == 3 else (None,))) for row in note.get(field, ())]


def _frontmatter_mutants(note):
    text = _frontmatter(note)
    body = _join(note['paragraphs'])
    lines = text.splitlines(keepends=True)
    yield body  # removed entirely
    yield ''.join(lines[:-1]) + body  # closing line dropped
    yield ''.join(lines[1:]) + body  # opening line dropped
    yield ''.join(line for line in lines if not line.startswith('title:')) + body  # required key dropped
    yield text.replace('status: active', 'status: unknown') + body  # enum violation
    yield text.replace('title: ', 'source: [[unquoted]]\ntitle: ') + body  # unquoted wiki link
    yield text.replace('title: ', '  title: ') + body  # broken indent
    yield _frontmatter(note, closing='----') + body  # four-dash closing line
    yield text.replace('type: concept', 'tags: [a, b\ntype: concept') + body  # unclosed bracket
    yield text.replace('title: ', 'title: "') + body  # unclosed quote
    yield text.replace('created: ', 'created: 1').replace('title: ', 'title: x') + body  # values changed


def mutants(note):
    """Every mutation of one note as {'id', 'category', 'text', 'claims', 'target'}; pure and deterministic."""
    head, paragraphs = _frontmatter(note), note['paragraphs']
    body = _join(paragraphs)
    made = []

    def add(category, text, claims=(), target=TARGET):
        made.append({'id': '%s/%s/%d' % (note['id'], category, sum(row['category'] == category for row in made)),
                     'category': category, 'text': text, 'claims': tuple(claims), 'target': target})

    for old, new, _ in _edits(note, 'values'):
        add('value_change', head + _replace(body, old, new))
    if note['wiki']:
        # Both values also sit in the frontmatter, which is what used to hide them.
        broken = _frontmatter(note, closing='----')
        add('value_change_behind_broken_fence', broken + _replace(body, note['date'], '2027-01-15'))
        add('value_change_behind_broken_fence', broken + _replace(body, '[[' + note['wiki'], '[[Changed Target'))
    for old, new, claim in _edits(note, 'unextracted'):
        add('value_change_unextracted', head + _replace(body, old, new), [claim])
    for old, new, _ in _edits(note, 'units_listed'):
        add('unit_change_listed', head + _replace(body, old, new))
    for old, new, claim in _edits(note, 'units_unlisted'):
        add('unit_change_unlisted', head + _replace(body, old, new), [claim])
    for first, second, claim in _edits(note, 'swaps'):
        add('swap', head + _swap(body, first, second), [claim])
    for old, new, claim in _edits(note, 'negations'):
        add('negation_insertion', head + _replace(body, old, new), [claim])
    for index, (text, facts) in enumerate(paragraphs):
        if not facts and not text.startswith('#'):
            add('fact_free_paragraph_deletion', head + _join(paragraphs[:index] + paragraphs[index + 1:]), [text])
    fact_rows = [index for index, (_, facts) in enumerate(paragraphs) if facts]
    if fact_rows:
        last, previous = fact_rows[-1], fact_rows[-2]
        add('tail_truncation_with_facts', head + _join(paragraphs[:last]))
        add('tail_truncation_with_facts', head + _join(paragraphs[:previous]))
        add('tail_truncation_with_facts', head + _join(paragraphs[:fact_rows[0]]))
        final = paragraphs[-1][0]
        add('tail_truncation_fact_free', head + _join(paragraphs[:-1]))
        add('tail_truncation_fact_free', head + _join(paragraphs[:-2]))
        add('tail_truncation_fact_free', head + _join(paragraphs[:-1]) + '\n' + final[:len(final) // 2])
    if note['wiki']:
        for text in _frontmatter_mutants(note):
            add('frontmatter_destruction', text)
            add('frontmatter_destruction_exempt_target', text, target=EXEMPT_TARGET)
    for old, new, _ in _edits(note, 'kept'):
        add('rewrite_literals_kept', head + _replace(body, old, new))
    if note['wiki']:
        add('rewrite_literals_kept', (head + body).replace('\n', '\r\n'))
        add('rewrite_literals_kept', head + body + '\n' + ('참고 문단을 덧붙인다.' if note['language'] == 'ko'
                                                            else 'One added reference paragraph.') + '\n')
        reordered = list(paragraphs)
        reordered[-1], reordered[-2] = reordered[-2], reordered[-1]
        add('rewrite_literals_kept', head + _join(reordered))
    for old, new, _ in _edits(note, 'reformatted'):
        add('rewrite_literals_reformatted', head + _replace(body, old, new))
    return made


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_quality():
    sys.dont_write_bytecode = True
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import vault_compile_quality
    return vault_compile_quality


def _classify(result):
    if result['status'] == 'complete':
        return 'survived'
    return 'unmeasured' if result['review_reasons'] == [NO_FACTS] else 'flagged'


def _library(control, quality):
    def check(original, candidate, claims=(), removals=()):
        if control == 'always_complete':
            return {'status': 'complete', 'review_reasons': []}
        if control == 'always_review':
            return {'status': 'needs_review', 'review_reasons': ['unexpected_removals']}
        return quality.check_preservation(original, candidate, required_claims=list(claims),
                                          expected_removals=list(removals))
    return check


class _Pipeline:
    """propose then preserve_check in a throwaway vault, in process for speed."""

    def __init__(self, vault):
        self.vault = Path(vault)
        (self.vault / '00-meta').mkdir()
        (self.vault / '20-knowledge').mkdir()
        (self.vault / '00-meta/vault-config.json').write_text(json.dumps(CONFIG), encoding='utf-8')
        load_quality()
        import vault_proposals
        self.proposals = vault_proposals
        self.store = vault_proposals.Store(self.vault)

    def run(self, target, original, candidate):
        (self.vault / target).write_bytes(original.encode('utf-8'))
        (self.vault / CANDIDATE).write_bytes(candidate.encode('utf-8'))
        request = SimpleNamespace(target=target, candidate=CANDIDATE, lesson='L-1', summary='fidelity')
        try:
            with self.store.lock():
                created = self.proposals.propose(self.store, request)
        except self.proposals.ProposalError:
            return 'blocked'
        receipt = self.store.load(created['id'])
        self.proposals.validate_change(self.store, receipt['target'], receipt['original'], receipt['candidate'])
        return _classify(self.proposals.preserve_check(receipt, [], [], []))


def _column(stats, outcomes, scope):
    counts = {name: sum(1 for item in outcomes if item == name)
              for name in ('flagged', 'survived', 'blocked', 'unmeasured')}
    trials = len(outcomes)
    events = counts['flagged'] if scope == 'false_alarm' else counts['survived']
    counts.update(events=events, event_rate=(events / trials if trials else None),
                  event_rate_upper_95=(stats.clopper_pearson_upper_bound(events, trials, confidence=CONFIDENCE)
                                       if trials else None))
    return counts


def _pooled(stats, rows, column):
    trials = sum(row['trials'] for row in rows)
    events = sum(row[column]['events'] for row in rows)
    return {'events': events, 'trials': trials, 'event_rate': events / trials if trials else None,
            'event_rate_upper_95': (stats.clopper_pearson_upper_bound(events, trials, confidence=CONFIDENCE)
                                    if trials else None)}


def evaluate(*, control=None):
    """Run the table. The report is deterministic: no clock, paths or random ids."""
    if control is not None and control not in CONTROLS:
        raise FidelityError('invalid_control')
    stats = _load(EVAL_STATS_PATH, 'verifier_fidelity_eval_stats')
    quality = load_quality()
    check = _library(control, quality)
    outcomes = {name: {'library': [], 'preserve_check': [], 'declared': []} for name, _, _ in CATEGORIES}
    measured = {'en': 0, 'ko': 0}
    excluded, survivors, alarms = [], [], []
    scopes = {name: scope for name, scope, _ in CATEGORIES}
    with tempfile.TemporaryDirectory(prefix='av-fidelity-') as temp:
        pipeline = None if control else _Pipeline(temp)
        for note in NOTES:
            original = render(note)
            baseline = check(original, original)
            if baseline['status'] != 'complete':
                if not note['wiki']:
                    # Delete each tail twice: undeclared, and with the deletion declared.
                    cuts = [(_frontmatter(note) + _join(note['paragraphs'][:keep]), note['paragraphs'][keep][0])
                            for keep in range(1, len(note['paragraphs']))]
                    seen = [_classify(check(original, text, removals=removals))
                            for text, phrase in cuts for removals in ((), (phrase,))]
                    excluded.append({'note': note['id'], 'review_reasons': baseline['review_reasons'],
                                     'mutants_flagged': seen.count('flagged'),
                                     'mutants_complete': seen.count('survived'),
                                     'mutants_unmeasured': seen.count('unmeasured')})
                continue
            measured[note['language']] += 1
            for mutant in mutants(note):
                row = outcomes[mutant['category']]
                outcome = _classify(check(original, mutant['text']))
                row['library'].append(outcome)
                if pipeline is None:
                    row['preserve_check'].append(outcome)
                else:
                    row['preserve_check'].append(pipeline.run(mutant['target'], original, mutant['text']))
                if mutant['claims']:
                    row['declared'].append(_classify(check(original, mutant['text'], mutant['claims'])))
                if scopes[mutant['category']] == 'in_scope' and outcome == 'survived':
                    survivors.append(mutant['id'])
                if scopes[mutant['category']] == 'false_alarm' and outcome == 'flagged':
                    alarms.append(mutant['id'])
    rows = []
    for name, scope, reason in CATEGORIES:
        data = outcomes[name]
        rows.append({'category': name, 'scope': scope, 'scope_reason': reason, 'trials': len(data['library']),
                     'library': _column(stats, data['library'], scope),
                     'preserve_check': _column(stats, data['preserve_check'], scope),
                     'declared': (_column(stats, data['declared'], scope) if data['declared'] else None)})
    summary = {}
    for scope in ('in_scope', 'out_of_scope', 'false_alarm'):
        chosen = [row for row in rows if row['scope'] == scope]
        summary[scope] = {'trials': sum(row['trials'] for row in chosen),
                          'library': _pooled(stats, chosen, 'library'),
                          'preserve_check': _pooled(stats, chosen, 'preserve_check')}
    return {'tool': 'evaluate_verifier_fidelity', 'version': 1, 'authority': AUTHORITY, 'model_calls': 0,
            'confidence': CONFIDENCE, 'control': control,
            'event_meaning': {'in_scope': 'miss', 'out_of_scope': 'miss', 'false_alarm': 'false alarm'},
            'notes': {'measured': measured, 'excluded': len(excluded)}, 'excluded_notes': excluded,
            'rows': rows, 'summary': summary, 'in_scope_survivors': survivors, 'false_alarm_cases': alarms}


def _percent(value):
    return '-' if value is None else '%.1f%%' % (100 * value)


def _table(report):
    header = ('category', 'scope', 'n', 'lib flag', 'pc flag', 'blocked', 'lib event', 'upper95', 'declared')
    lines = ['Synthetic fixture measurement. Not evidence of real-world safety.',
             'event = miss for mutations, false alarm for rewrites; upper95 = one-sided exact bound.',
             '%-38s %-13s %4s %8s %7s %7s %9s %7s %8s' % header]
    for row in report['rows']:
        declared = row['declared']
        lines.append('%-38s %-13s %4d %8d %7d %7d %9s %7s %8s' % (
            row['category'], row['scope'], row['trials'], row['library']['flagged'],
            row['preserve_check']['flagged'], row['preserve_check']['blocked'],
            _percent(row['library']['event_rate']), _percent(row['library']['event_rate_upper_95']),
            '%d/%d' % (declared['flagged'], row['trials']) if declared else '-'))
    return '\n'.join(lines)


def _parser():
    parser = _Parser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--json', action='store_true', help='print the report as JSON')
    parser.add_argument('--control', choices=CONTROLS, help='replace the check with a fixed answer')
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        report = evaluate(control=args.control)
    except FidelityError as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 2
    print(json.dumps(report, sort_keys=True) if args.json else _table(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
