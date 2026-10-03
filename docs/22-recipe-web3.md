# Recipe: wallets and web3

PyWeb has nothing web3-specific built in, and doesn't need it: wallet
libraries are npm packages, and verifying a signature is a few lines of
Python. This recipe signs users in with their Ethereum wallet ("Sign-In
with Ethereum" style) using [ethers](https://docs.ethers.org) in the
browser and [eth-account](https://pypi.org/project/eth-account/) on the
server.

## Install

```text
pip install eth-account
pyweb add ethers
```

`pyweb add` vendors ethers and its dependencies (about 150 files) into
`static/vendor/`; pages that don't use it don't load it.

## The app

```pyweb
import secrets
import time

from eth_account import Account
from eth_account.messages import encode_defunct

from pyweb import App, RPCError, npm, server, session

ethers = npm("ethers", "*")
app = App(title="Sign in with a wallet")
NONCES = {}   # nonce -> expiry; use your database or cache with several processes


@server
def challenge() -> str:
    """A one-time message for the wallet to sign."""
    nonce = secrets.token_hex(16)
    NONCES[nonce] = time.time() + 300
    return f"Sign in to {app.title}\nNonce: {nonce}"


@server
def sign_in(message: str, signature: str) -> str:
    nonce = message.rsplit("Nonce: ", 1)[-1]
    if NONCES.pop(nonce, 0) < time.time():
        raise RPCError("unauthenticated", "that sign-in request expired; try again")
    address = Account.recover_message(encode_defunct(text=message), signature=signature)
    session.login(address)
    return address


@server
def sign_out() -> None:
    session.logout()


@app.page("/")
def Home():
    user = session.user()
    address = user["sub"] if user else ""
    error = ""

    async def connect():
        error = ""
        if not window.ethereum:
            error = "No wallet found: install one such as MetaMask or Rabby."
            return
        try:
            provider = ethers.BrowserProvider(window.ethereum)
            signer = await provider.getSigner()
            message = challenge()
            signature = await signer.signMessage(message)
            address = sign_in(message, signature)
        except RPCError as e:
            error = str(e)

    def leave():
        sign_out()
        address = ""

    <main>
        if address:
            <p id="who">Signed in as <code>{address}</code></p>
            <button onclick={leave}>Sign out</button>
        else:
            <button id="connect" onclick={connect}>Connect wallet</button>
        <p class="error">{error}</p>
    </main>
```

How it works:

1. **The server makes a one-time message** (`challenge`). The nonce
   stops a signature from being reused, and it expires after five
   minutes.
2. **The wallet signs it in the browser.** `ethers.BrowserProvider`
   talks to whatever wallet the visitor has installed (it injects
   `window.ethereum`); `signMessage` asks them to approve.
3. **The server recovers the address from the signature**
   (`Account.recover_message`) and signs the visitor in with it as their
   user id. The private key never leaves the wallet, and the browser can't
   claim an address it can't sign for.

From there, `session.user()["sub"]` is the wallet address in any page or
server function.

## Reading the chain

For balances, contract calls and transactions, use ethers in browser
code, through the visitor's wallet:

```pyweb
from pyweb import App, npm

ethers = npm("ethers", "*")
app = App()


@app.page("/balance")
def Balance():
    balance = ""

    async def check():
        provider = ethers.BrowserProvider(window.ethereum)
        signer = await provider.getSigner()
        wei = await provider.getBalance(await signer.getAddress())
        balance = ethers.formatEther(wei) + " ETH"

    <button onclick={check}>Show my balance</button>
    <p>{balance}</p>
```

Or read the chain from the server with any Python library (for example
`web3.py` with an RPC provider URL in an environment variable) inside
`@server` functions, which keeps provider keys off the page.

## Notes

- Keep `NONCES` somewhere shared (your database, or `pyweb.cache` with
  Redis) when you run several processes.
- Use the address as the user id, and store profile data in your own
  tables keyed by it.
- The same pattern works for other chains with their own npm wallet
  library and Python verification package.
