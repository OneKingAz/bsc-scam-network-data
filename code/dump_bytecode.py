"""Saves the deployed bytecode of a token (public data from the node) next to this script. Run: python analysis\\dump_bytecode.py <token>"""
import contextlib, io, os, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
T = sys.argv[1]
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
from web3 import Web3

t = Web3.to_checksum_address(T)
code = bytes(B.w3.eth.get_code(t))
slot = B.w3.eth.get_storage_at(t, 6).hex()[-40:]
out = os.path.join(HERE, "token_bytecode.hex")
with open(out, "w", encoding="ascii") as f:
    f.write("0x" + code.hex() + "\n")
print(t, "байт", len(code), "keccak", Web3.keccak(code).hex(), "слот 6: 0x" + slot)
print(out)
