import requests

def get_district_from_pincode(pincode: str):
    try:
        url = f"https://api.postalpincode.in/pincode/{pincode}"
        res = requests.get(url).json()

        if res[0]["Status"] != "Success":
            return None

        po = res[0]["PostOffice"][0]

        return {
            "district": po["District"],
            "state": po["State"]
        }

    except:
        return None