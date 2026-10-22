from finwhiz.answer_validation import referenced_sources


def test_sources_follow_visible_references_and_preserve_ids():
    sources = [{'id': f'S{i}'} for i in range(1, 7)]
    answer = {
        'answer': 'Supported statement [S3]. Another statement [S3].',
        'citation_ids': ['S1', 'S3'],  # A model's unused declaration is insufficient.
        'calculations': [{'source_ids': ['S5']}],
        'chart': {'type': 'bar', 'points': [{'source_ids': ['S2']}]},
    }
    assert referenced_sources(answer, sources) == [sources[1], sources[2], sources[4]]
    assert len(sources) == 6  # Retrieval trace remains intact.


def test_no_references_means_no_listed_evidence():
    answer = {'answer': 'Insufficient evidence.', 'citation_ids': ['S1'],
              'chart': {'type': 'none', 'points': [{'source_ids': ['S1']}]}}
    assert referenced_sources(answer, [{'id': 'S1'}]) == []
