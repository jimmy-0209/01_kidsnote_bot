import os

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

_fernet = Fernet(os.environ["KIDSNOTE_PW_ENC_KEY"].encode())


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
