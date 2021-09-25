import zlib


def pako_inflate(data):
  decompress = zlib.decompressobj(15)
  decompressed_data = decompress.decompress(data)
  decompressed_data += decompress.flush()
  return decompressed_data
