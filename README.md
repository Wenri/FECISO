# FECISO

Create ISO with Forward Error Correction (FEC) code appended. 
Optionally you can compress and encrypt your files while preparing the ISO file.

FEC code will help you to recover your data in case of corruption.
For more details, please refer to [this article](https://gist.github.com/Wenri/806a26ed2a5ca1c9b7d81b7f78a39a88).

## Requirements

* Python 3.9
* python3-psutil
* python3-tqdm
* python3-numpy
* cryptsetup

The dependencies can be installed via:

```shell
apt install python3-psutil python3-tqdm python3-numpy cryptsetup
```

## Usage

To create an ISO that compress and encrypt your data, use

```shell
main.py -V My_Disc_Label -o My_Disc.iso -C 'p@Ssw0rd' --hint 'Password Hint' '/path/to/data_dir'
```

The created ISO contains an ordinary ISO9660 filesystem. 
It can be read as usual by most Operating System like Windows, macOS or Linux.
However, the validation and error correction is done by dm-verity, which is a part of Linux kernel.
To enable this function, the device-mapper needs to be created from ISO by veritysetup with correct offset.
A helper bash script `boot.sh` template has been filled with correct parameter and written to the beginning of ISO
during the creation process. To use this helper script, simply execute it with 

```shell
bash My_Disc.iso
```

If you burn this ISO to a disc, you can directly execute this helper from device node

```shell
bash /dev/sr0
```

This script will also setup dm-crypt for decryption if encryption is enabled during the creation of ISO.
