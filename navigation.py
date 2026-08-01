import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import locators as loc
import config


def tap(driver, waiter, locator, label):
    el = waiter.until(EC.presence_of_element_located(locator))
    el.click()
    print(f"Tapped: {label}")
    time.sleep(1)


def navigate_to_test(driver, wait, wait_long):
    tap(driver, wait, loc.PROGRAM_CERTIFICATE, "Program Certificate")
    tap(driver, wait, loc.GET_CERTIFICATE, "Get certificate")

    try:
        WebDriverWait(driver, 15).until(
            EC.invisibility_of_element_located(loc.PROGRESS_BAR)
        )
        print("Spinner gone, list loaded")
    except TimeoutException:
        print("Spinner still visible after 15s, continuing anyway")

    time.sleep(1)

    course_selector = (
        AppiumBy.ANDROID_UIAUTOMATOR,
        f'new UiSelector().description("{config.COURSE_DESCRIPTION}")'
    )
    tap(driver, wait_long, course_selector, "Course selected")

    test_selector = (AppiumBy.ACCESSIBILITY_ID, config.TEST_NAME)
    tap(driver, wait, test_selector, "Test selected")

    tap(driver, wait, loc.START_TEST, "Start test")

    print("Reached the question screen!")
