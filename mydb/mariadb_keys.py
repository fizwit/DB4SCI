import os
import shutil
import subprocess
from hashlib import md5

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from . import mydb_config

""" create keys for MariaDB encryption at rest

generate a random 128-bit encryption key with:  openssl rand 16 -hex >key.txt
# encrypt the key file
openssl enc -aes-256-cbc -P -md sha1
enter aes-256-cbc encryption password:
Verifying - enter aes-256-cbc encryption password:
salt=2C573F713EE0C3FB
key=53FA6507235FDA0B9D878B2CE6415A0B5B9F79CA73E22735FCEEA023EEF83602
iv =C9051F2BB4D4B18815EBE9426486CAB0

save like this: keys.txt
1;C9051F2BB4D4B18815EBE9426486CAB0;53FA6507235FDA0B9D878B2CE6415A0B5B9F79CA73E22735FCEEA023EEF83602

==== earlier notes =======
openssl enc -aes-256-cbc -md sha256 -pbkdf2 -pass env:MYDB_KEYPASS -in keys.txt -out keys.enc

[mysqld]
file_key_management_encryption_algorithm=aes_cbc
file_key_management_filename = /home/mdb/keys.enc
file_key_management_filekey = secret
"""

# Password openssl uses to encrypt keys.enc; must match
# file_key_management_filekey in the server config.
FILE_KEY_PASSWORD = "mydbencrypt"

# Key derivation openssl uses for keys.enc.  This must match what the
# file_key_management plugin expects, which changed in MariaDB 12.0.1:
#   12.0.1 and later:  ["-md", "sha256", "-pbkdf2"]
#   before 12.0.1:     ["-md", "sha1"]
OPENSSL_KDF_ARGS = ["-md", "sha256", "-pbkdf2"]


def copy_tsl(con_name, db_vol):
    """Copy TLS server keys to mairadb keys volume
    Copy any MariaDB configure files to /etc/mysql/conf.d volume
    """
    files = ['server-cert.pem', 'server-key.pem', 'server-req.pem',
             'ca.pem']
    source = mydb_config.db4sci_path + '/TLS/'
    destination = db_vol + '/' + con_name + '/keys/'
    for fname in files:
        print("copy %s %s" % (source + fname, destination))
        shutil.copy(source + fname, destination)
        os.chown(destination + fname, 999, 999)
        os.chmod(destination + fname, 0o600)
    destination = db_vol + '/' + con_name + '/conf.d/'
    source = mydb_config.db4sci_path + '/dbconfig/MariaDB/'
    files = os.listdir(source)
    for fname in files:
        shutil.copy(source + fname, destination)


def encrypt_key_file(key_file, enc_file, password):
    """ Encrypt contents of <key_file> and write to <enc_file>

    The password is passed through the environment rather than argv so it
    does not show up in "ps".
    """
    cmd = ['openssl', 'enc', '-aes-256-cbc'] + OPENSSL_KDF_ARGS + [
        '-pass', 'env:MYDB_KEYPASS', '-in', key_file, '-out', enc_file]
    print('encrypting keys: %s' % ' '.join(cmd))
    env = dict(os.environ, MYDB_KEYPASS=password)
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError('openssl enc failed (%d): %s' %
                           (result.returncode, result.stderr.strip()))


def derive_key_and_iv(password):
    """ Derive 32 byte key and 16 byte iv from <password> and a random salt

    OpenSSL EVP_BytesToKey(MD5, 1 iteration).  The salt is random on every
    call, so the result is not reproducible from <password> alone.
    """
    iv_length = AES.block_size
    key_length = 32
    salt = get_random_bytes(iv_length - len('Salted__'))
    d = d_i = b''
    while len(d) < key_length + iv_length:
        d_i = md5(d_i + password.encode('ascii', 'ignore') + salt).digest()
        d += d_i
    return salt, d[:key_length], d[key_length:key_length+iv_length]


def create_mariadb_key(con_name, params):
    """ create key, save to file, encrypt key file
        then delete key
    """
    password = params['dbuserpass']
    db_vol = params['db_vol']
    salt, key, iv = derive_key_and_iv(password)
    iv_str = iv.hex().upper()
    key_str = key.hex().upper()
    key_path = db_vol + '/' + con_name + '/keys'
    keyfile = key_path + '/keys.txt'
    encname = key_path + '/keys.enc'
    copy_tsl(con_name, db_vol)
    # write keys.txt
    iv_key = '1;' + iv_str + ';' + key_str
    with open(keyfile, 'w') as filep:
        filep.write('# this is a comment\n')
        filep.write(iv_key + '\n')
    # encrypt keys.txt -> keys.enc
    encrypt_key_file(keyfile, encname, FILE_KEY_PASSWORD)

    # delete the unencrypted file
    os.remove(keyfile)

    # correct owner and permissions
    os.chown(encname, 999, 999)
    os.chmod(encname, 0o700)
    return iv_key


if __name__ == '__main__':
    key = create_mariadb_key('test1', {'dbuserpass': 'mydbencrypt',
                                       'db_vol': mydb_config.db4sci_path + '/db_vol'})
    print('encryption key: {}'.format(key))
