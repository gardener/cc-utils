import base64


import oci.client as co


def test_append_b64_padding_if_missing():
    def encode_and_decode(octets: bytes):
        encoded = base64.b64encode(octets).decode('utf-8')
        encoded_wo_padding = encoded.strip('=')

        assert encoded == co._append_b64_padding_if_missing(encoded_wo_padding)

    encode_and_decode(b'a')
    encode_and_decode(b'ab')
    encode_and_decode(b'abc')
    encode_and_decode(b'abcd')
