import ellipticcurve
import os
from cryptography.hazmat.primitives import padding
import numbertheory
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# secp256k1 elliptic curve equation: y² = x³ + 7
# Prime of the finite field
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
# Since P % 4 = 3, the square root of x = z^2 satisfies that z = x^q
q = (P + 1) // 4

# Define curve secp256k1
secp256k1 = ellipticcurve.CurveFp(P, 0, 7)
# Generator point of secp256k1
G = ellipticcurve.Point(
    x=0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    y=0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
    curve=secp256k1
)
# Order of the group generated by G, such that nG = Infinity
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


# Key generation algorithm
# Generate an epoch key
def keygen():
    epoch_key_found = False
    epoch_key = None
    inverse_epoch_key = None
    while not epoch_key_found:
        try:
            epoch_key = int.from_bytes(os.urandom(32), 'big')  # for encryption
            inverse_epoch_key = numbertheory.inverse_mod(epoch_key, N)  # for decryption
            epoch_key_found = True
        except AssertionError:
            continue
    return epoch_key, inverse_epoch_key


# Token generation algorithm
# Compute the update token
def tokengen(key1, key2):
    token = (key1[1] * key2[0]) % N
    return token


# Encryption algorithm
# Encrypt short data
def enc(key, data, permutation):
    # The data block should be 16 bytes,
    if len(data) < 16:
        padder = padding.PKCS7(128).padder()
        data = padder.update(data)
        data += padder.finalize()
    elif len(data) > 16:
        print('Data is too long!')

    # If there is no point with the x-coordinate on the curve, re-generate a new N and an x-coordinate
    pt = None
    point_found = False
    while not point_found:
        try:
            encryptor = permutation.encryptor()
            bytes_pi_data = encryptor.update(os.urandom(16) + data) + encryptor.finalize()
            int_pi_data = int.from_bytes(bytes_pi_data, 'big')
            # Embed the permutation output to the x-coordinate of an elliptic curve point
            # for exponentiation by the epoch key
            x = int_pi_data
            # Find a point on the curve with x-coordinate
            y = pow((x ** 3 + 7) % P, q, P)
            pt = ellipticcurve.Point(x=x, y=y, curve=secp256k1)
            point_found = True
        except AssertionError:
            continue

    # Encrypt the point by the epoch key and get k * pt
    c = key[0] * pt
    return c


# Decryption algorithm
def dec(key, c, permutation):
    # Decrypt the ciphertext and gets the original point pt, denote ptt
    ptt = key[1] * c
    # The x-coordinate of point ptt is the permutation output Pi(N|data)
    bytes_pi_m = ptt._Point__x.to_bytes(32, 'big')
    # Inverse the permutation Pi to get the underlying data
    # AES decryption algorithm simulates the inverse permutation
    decryptor = permutation.decryptor()
    decrypted_data = decryptor.update(bytes_pi_m) + decryptor.finalize()

    # Unpad the last message block
    unpadder = padding.PKCS7(128).unpadder()
    decrypted_data = unpadder.update(decrypted_data)
    decrypted_data = decrypted_data + unpadder.finalize()
    result = decrypted_data[16:32]
    return result


# Update algorithm
def upd(token, c):
    c_new = token * c
    return c_new


# OCBSHINE is suitable for encrypting big data
# Encryption algorithm
def ocb_enc(key, data, pi_key, permutation):
    # Split data into l blocks, each data block should be 31 bytes,
    # the last data block should be padded to 31 bytes
    # Leave one byte space for each block, we need this nonce space to find a valid elliptic curve point later
    n = 31
    split_data = [data[i:i + n] for i in range(0, len(data), n)]
    if len(split_data[-1]) < n:
        padder = padding.PKCS7(128).padder()
        split_data[-1] = padder.update(b' ' + split_data[-1])
        split_data[-1] += padder.finalize()
        split_data[-1] = split_data[-1][1:32]

    num_data = len(split_data)
    pt = dict()
    N = os.urandom(15)

    # Permutation of nonce N
    point_found = False
    while point_found == False:
        try:
            encryptor = permutation.encryptor()
            bytes_pi_data = encryptor.update(N + os.urandom(17)) + encryptor.finalize()
            int_pi_N = int.from_bytes(bytes_pi_data, 'big')
            x = int_pi_N
            y = pow((x ** 3 + 7) % P, q, P)
            pt[num_data] = ellipticcurve.Point(x=x, y=y, curve=secp256k1)
            point_found = True
        except AssertionError:
            continue

    # Permutation of each message block
    msg_permutation = dict()
    for i in range(num_data):
        N_i = N + i.to_bytes(1, 'big')
        msg_permutation[i] = Cipher(algorithms.AES(pi_key), modes.CBC(N_i))
        point_found = False
        while point_found == False:
            try:
                encryptor = msg_permutation[i].encryptor()
                # Sample os.urandom(1) to obtain a permutation output that can be embedded to an elliptic curve point
                bytes_pi_data = encryptor.update(os.urandom(1) + split_data[i]) + encryptor.finalize()
                int_pi_data = int.from_bytes(bytes_pi_data, 'big')
                x = int_pi_data
                y = pow((x ** 3 + 7) % P, q, P)
                pt[i] = ellipticcurve.Point(x=x, y=y, curve=secp256k1)
                point_found = True
            except AssertionError:
                continue

    # Encrypt the point by the epoch key and get k * pt
    c = dict()
    for i in range(num_data + 1):
        c[i] = key[0] * pt[i]
    return c


# OCBSHINE
# Decryption algorithm
def ocb_dec(key, c, pi_key, permutation):
    Ptt = dict()
    result = bytes()
    num_block = len(c) - 1
    # Decrypt the nonce
    Ptt[num_block] = key[1] * c[num_block]
    bytes_pi_N = Ptt[num_block]._Point__x.to_bytes(32, 'big')
    decryptor = permutation.decryptor()
    decrypted_N = decryptor.update(bytes_pi_N) + decryptor.finalize()
    N = decrypted_N[:15]

    # Decrypt each message block
    msg_permutation = dict()
    for i in range(num_block):
        N_i = N + i.to_bytes(1, 'big')
        msg_permutation[i] = Cipher(algorithms.AES(pi_key), modes.CBC(N_i))
        Ptt[i] = key[1] * c[i]
        # The x-coordinate of point Ptt is the permutation output Pi(N|data)
        bytes_pi_m = Ptt[i]._Point__x.to_bytes(32, 'big')

        # Inverse the permutation Pi to get the underlying data
        # AES decryption algorithm simulates the inverse permutation
        decryptor = msg_permutation[i].decryptor()
        decrypted_data = decryptor.update(bytes_pi_m) + decryptor.finalize()
        if i != num_block - 1:
            result = result + decrypted_data[1:32]
    unpadder = padding.PKCS7(128).unpadder()
    decrypted_data = unpadder.update(decrypted_data)
    decrypted_data = decrypted_data + unpadder.finalize()
    result = result + decrypted_data[1:32]
    return result


# Update algorithm
def ocb_upd(token, c):
    c_new = dict()
    for i in range(len(c)):
        c_new[i] = token * c[i]
    return c_new
