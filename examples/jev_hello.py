import os
import time
import requests
import json


API_URL = "https://api.typesafe.ai/v1/systemone"


def main():
    api_key = os.environ["TYPESAFE_API_KEY"]

    payload = {
        "state": "I am hungry. Should I eat something?",
        "model": "jev-latest",
        "questions": {
            "new_choice_1": {
                "type": "choice",
                "instructions": "Which option best fits the state?",
                "criteria": {
                    "option_a": "eat",
                    "option_b": "not eat",
                    "option_c": "eat a little",
                },
            }
        },
    }

    start = time.perf_counter()
    response = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    elapsed = time.perf_counter() - start

    print("HTTP:", response.status_code)
    print(f"Elapsed: {elapsed:.3f}s")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

