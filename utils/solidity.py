# solidity units
seconds = 1
minutes = 60 * seconds
hours = 60 * minutes
days = 24 * hours
weeks = 7 * days
years = 365 * days


def to_float(n, decimals=18):
  return n / 10 ** decimals


def to_int(n, decimals=18):
  return n // 10 ** decimals
