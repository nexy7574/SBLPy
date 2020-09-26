import argparse, os, json

parser = argparse.ArgumentParser()

def new_auth_config():
    b = lambda x: x.lower().startswith("y")
    if b(input("Would you like to create an authentication configuration file? [Y/N]\n> ")):
        if not os.path.exists("./.sblpy"):
            os.mkdir("./.sblpy")
        if os.path.exists("./.sblpy/auth_config.json"):
            if not b(input("An authentication file already exists. Are you sure you want to overwrite it? [Y/N]\n> ")):
                exit(0)
        to_dump = {}
        while True:
            print("Please enter the URI (or slug) of the bot you wish to authorise. Enter \"finish\" if you are done.")
            key = input("> ")
            if key.lower() == "finish":
                break
            value = input(f"What is {key}'s authentication token?\n> ")
            to_dump[key] = value
            print(f"\N{white heavy check mark} Added {key} to auth.")
        with open("./sblpy/auth_config.json", "w+") as wfile:
            json.dump(to_dump, wfile)
        print(f"Successfully added {len(to_dump)} bots to the authentication file.")
        exit(0)

parser.add_argument("--auth", "-A", action="store_true")

ns = parser.parse_args()
if ns.auth:
    new_auth_config()
