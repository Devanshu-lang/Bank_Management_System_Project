# Create an Account or Register
from pathlib import Path
import json
import random
import string


class Bank:
    database = "database.json"
    data = []

    try:
        if Path(database).exists():
            with open(database) as fs:
                data = json.loads(fs.read())
    except Exception as err:
        print(f"An error occurred as {err}, try again")

    @classmethod
    def __update(cls):
        with open(cls.database, "w") as fs:
            fs.write(json.dumps(cls.data))

    @staticmethod
    def __generate_accountno():
        char = random.choices(string.ascii_uppercase, k=4)
        digits = random.choices(string.digits, k=8)
        acc = char + digits
        final = "".join(acc)
        return final

    def create_account(self):
        info = {
            "name": input("Enter your name :- "),
            "age": int(input("Enter your age :- ")),
            "mail": input("Enter your mail :- "),
            "balance": 0,
            "accountno.": Bank.__generate_accountno(),
            "number": int(input("Tell me your 10 digit number :- "))
        }

        try:
            while True:
                pin = int(input("Enter your 4 digit pin :- "))
                if len(str(pin)) != 4:
                    print("Your pin must be of 4 digits, please try again.")
                else:
                    info["pin"] = pin
                    break
        except Exception as err:
            print("You can only have 4 numbers. Try again.")

        if info["age"] < 18:
            print("You are a minor.")
            return
        else:
            Bank.data.append(info)
            Bank.__update()

    def deposite_money(self):
        acc_no = input("Tell your account number :- ")
        pin = int(input("Tell your pin :- "))

        user = [i for i in Bank.data if i["pin"] == pin and i["accountno."] == acc_no]

        if user:
            money = int(input("How much money you want to deposit :- "))

            if money > 10000 or money <= 0:
                print("You can't deposit more than 10,000 or less than zero.")
            else:
                user[0]["balance"] += money
                print("Money added successfully. Thanks, visit again!")
                Bank.__update()
        else:
            print("Invalid account number or pin.")

    def withdraw_money(self):
        acc_no = input("Tell your account number :- ")
        pin = int(input("Tell your pin :- "))
        user = [i for i in Bank.data if i["pin"] == pin and i["accountno."] == acc_no]

        if user:
            money = int(input("How much money you want to withdraw :- "))

            if money > user[0]["balance"] or money <= 0:
                print("Insufficient Balance.")
            else:
                user[0]["balance"] -= money
                print("Money debited from your account.")
                Bank.__update()
        else:
            print("Invalid account number or pin.")

    def check_details(self):
        acc_no = input("Tell your account number :- ")
        pin = int(input("Tell your pin :- "))

        user = [i for i in Bank.data if i["pin"] == pin and i["accountno."] == acc_no]

        if user:
            print("Your details are : \n")
            for i in user[0]:
                if i != "pin":
                    print(f"{i} : {user[0][i]}")
        else:
            print("Invalid Account no. or pin")

    # FIX 1: added `self` — this is called as bank.update_details(), so Python
    # passes the instance automatically. Without `self` here, that call would
    # raise "takes 0 positional arguments but 1 was given".
    def update_details(self):
        acc_no = input("Tell your account number :- ")
        pin = int(input("Tell your pin :- "))
        user = [i for i in Bank.data if i["pin"] == pin and i["accountno."] == acc_no]

        # FIX 2: `user == False` never worked — a list is never equal to the
        # boolean False, so this branch could never trigger. Use `not user`
        # to correctly detect an empty (no-match) list.
        if not user:
            print("Invalid number or pin ")
        else:
            newdata = {
                "name": input("Enter to Skip or type your new name: "),
                "mail": input("Enter to Skip or type your new mail: "),
                "number": input("Enter to Skip or type your new number: "),
                "pin": input("Enter to Skip or type your new pin: "),
            }

            # FIX 3: these were `==` (comparison) instead of `=` (assignment),
            # so "leave blank to keep the old value" never actually happened.
            if newdata["name"] == "":
                newdata["name"] = user[0]["name"]
            if newdata["mail"] == "":
                newdata["mail"] = user[0]["mail"]
            if newdata["number"] == "":
                newdata["number"] = str(user[0]["number"])
            if newdata["pin"] == "":
                newdata["pin"] = str(user[0]["pin"])

            newdata["pin"] = int(newdata["pin"])
            newdata["number"] = int(newdata["number"])

            # FIX 4: this loop and the save call were indented to run
            # unconditionally, even when no account was found (which would
            # crash on `user[0]` with an empty list). Moved inside `else`,
            # so it only runs once we know `user` has a match.
            for i in user[0]:
                if i in newdata:
                    user[0][i] = newdata[i]

            # FIX 5: `Bank.update()` doesn't exist — the real method is
            # name-mangled to `Bank.__update()` because of the double
            # leading underscore. Written here (inside the class), Python
            # resolves `Bank.__update()` to the correct method automatically.
            Bank.__update()

    # FIX 1 (again): added `self` for the same reason as update_details.
    def delete_user(self):
        acc_no = input("Tell your account number :- ")
        pin = int(input("Tell your pin :- "))
        user = [i for i in Bank.data if i["pin"] == pin and i["accountno."] == acc_no]

        # FIX 2 (again): same `not user` fix as above.
        if not user:
            print("Invalid number ot pin")
        else:
            print("Are you sure? ")
            check = input("Press (Y) or (N) ")
            if check == "Y" or check == "y":
                # FIX 6: `user` is a list like [{...}], not the dict itself,
                # so Bank.data.index(user) would never find a match and
                # raises ValueError. Use user[0], the actual matching record.
                index = Bank.data.index(user[0])
                Bank.data.pop(index)
                Bank.__update()
            else:
                print("Ok")


bank = Bank()

print("Press 01 for Creating an Account")
print("Press 02 for Depositing Money")
print("Press 03 for Withdrawal Money")
print("Press 04 for Checking Balance")
print("Press 05 for Updating some details")
print("Press 06 for Deactivate your account")
print("Press 0 to Exit")

check = int(input("How may I help you? Type any one option from above :- "))

if check == 1:
    bank.create_account()

if check == 2:
    bank.deposite_money()

if check == 3:
    bank.withdraw_money()

if check == 4:
    bank.check_details()

if check == 5:
    bank.update_details()

if check == 6:
    # FIX 7: this called `bank.deleteuser()` (no underscore) — a method
    # that doesn't exist. The real method is `delete_user`.
    bank.delete_user()
