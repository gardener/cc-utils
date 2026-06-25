import github.pullrequest as gpr


def test_split_chunks_if_too_long():
    assert gpr.split_into_chunks_if_too_long(
        string='abcd',
        split_hint='does-not-matter',
        max_leng=100,
        max_chunk_leng=100,
    ) == ('abcd', ())

    assert gpr.split_into_chunks_if_too_long(
        string='12345678',
        split_hint='X',
        max_leng=2,
        max_chunk_leng=3,
    ) == ('1X', ('234', '567', '8'))
