# Supercell-Data
## Script to automate data collection from Supercell accounts system
*Mostly vibe-coded, but it does the job*
---
## Intro
[Supercell](https://support.supercell.com/) uses JWT to authenticate users, with an added CSRF layer to prevent cross site attacks. Having the user's JWT is enough to both satisfy CSRF requirements and authenticate in two steps.

The rest of the code retrieves the exported account data from the email sent to the user. Its specifically meant to parse Hay Day metrics, so those are saved into a json file.
---
## Install
### Clone the repo
```bash
git clone https://github.com/elemermelada/supercell-data
cd supercell-data
```
### (Optional) Set up new python environment
Find online how to do this for your specific platform and python distribution.
### Install dependencies
> (If using pip, check first!!)
```bash
pip install -r requirements.txt
```
### Set up environment variable
Rename .env.example into .env and set the right values.
### Run
```bash
python main.py
```
