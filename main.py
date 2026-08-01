import json
import time

from appium import webdriver
from appium.options.android import UiAutomator2Options
from selenium.webdriver.support.ui import WebDriverWait

import config
from navigation import navigate_to_test
from question_handler import get_screen_type, answer_multiple_choice, answer_ordering


def load_answer_bank():
    with open("answer_bank.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("multiple_choice", {}), data.get("ordering", {})


def main():
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = config.DEVICE_NAME
    options.app_package = config.APP_PACKAGE
    options.app_activity = config.APP_ACTIVITY
    options.no_reset = True

    driver = webdriver.Remote(config.APPIUM_SERVER, options=options)

    try:
        driver.terminate_app(config.APP_PACKAGE)
        time.sleep(1)
    except Exception as e:
        print(f"terminate_app skipped: {e}")

    driver.activate_app(config.APP_PACKAGE)
    time.sleep(3)

    wait = WebDriverWait(driver, 20)
    wait_long = WebDriverWait(driver, 30)

    navigate_to_test(driver, wait, wait_long)

    answer_bank_mc, answer_bank_ordering = load_answer_bank()

    max_questions = 1000
    for i in range(max_questions):
        screen_type = get_screen_type(driver)
        print(f"\n[{i+1}] Screen type: {screen_type}")

        if screen_type == "multiple_choice":
            ok = answer_multiple_choice(driver, wait, answer_bank_mc)
        else:
            ok = answer_ordering(driver, wait, answer_bank_ordering)

        if not ok:
            print("Stopped — answer not found or element missing.")
            break

    print("\nDone (or stopped early). Leaving session open for inspection.")
    # driver.quit()


if __name__ == "__main__":
    main()
