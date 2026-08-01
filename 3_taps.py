from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import time

options = UiAutomator2Options()
options.platform_name = "Android"
options.automation_name = "UiAutomator2"
options.device_name = "ZY22GTXB9R"
options.app_package = "uz.ibrat.farzandlari"
options.app_activity = ".MainActivity"
options.no_reset = True

driver = webdriver.Remote("http://127.0.0.1:4723", options=options)

driver.press_keycode(4)
time.sleep(1)
driver.activate_app("uz.ibrat.farzandlari")
time.sleep(3)

wait = WebDriverWait(driver, 20)
wait_long = WebDriverWait(driver, 30)


def tap_by_id(value, label, waiter=None):
    w = waiter or wait
    el = w.until(EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, value)))
    el.click()
    print(f"Tapped: {label}")
    time.sleep(1)


def tap_by_uiautomator(selector, label, waiter=None):
    w = waiter or wait
    el = w.until(EC.presence_of_element_located((AppiumBy.ANDROID_UIAUTOMATOR, selector)))
    el.click()
    print(f"Tapped: {label}")
    time.sleep(1)


def dismiss_feedback_and_continue(driver, wait):
    """
    Handles the feedback banner (correct or incorrect) that appears after
    answering, and taps whatever button advances to the next question.
    """
    locators = [
        (AppiumBy.ACCESSIBILITY_ID, "Next"),
        (AppiumBy.ACCESSIBILITY_ID, "Continue"),
        (AppiumBy.ACCESSIBILITY_ID, "OK"),
        (AppiumBy.XPATH, "//android.widget.Button[@content-desc='Next']"),
        (AppiumBy.XPATH, "//android.widget.Button[@content-desc='Continue']"),
        (AppiumBy.XPATH, "//*[@text='Next']"),
        (AppiumBy.XPATH, "//*[@text='Continue']"),
    ]

    for by, value in locators:
        try:
            btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((by, value))
            )
            btn.click()
            print(f"Advanced via: {value}")
            time.sleep(1.5)
            return True
        except TimeoutException:
            continue

    print("Could not find a button to advance past feedback screen")
    return False


def check_answer_feedback(driver):
    """Returns 'correct', 'incorrect', or 'unknown' based on visible feedback."""
    correct_indicators = driver.find_elements(
        AppiumBy.XPATH,
        "//*[contains(@text,'Nicely done') or contains(@content-desc,'Nicely done')]"
    )
    if correct_indicators:
        return "correct"

    incorrect_indicators = driver.find_elements(
        AppiumBy.XPATH,
        "//*[contains(@text,'Incorrect') or contains(@text,'Try again') or contains(@content-desc,'Incorrect')]"
    )
    if incorrect_indicators:
        return "incorrect"

    return "unknown"


# --- NAVIGATION TO TEST ---
tap_by_id("2+6 Program Certificate", "Program Certificate")
tap_by_id("Get certificate", "Get certificate")

try:
    WebDriverWait(driver, 15).until(
        EC.invisibility_of_element_located((AppiumBy.CLASS_NAME, "android.widget.ProgressBar"))
    )
    print("Spinner gone, list loaded")
except TimeoutException:
    print("Spinner still visible after 15s, continuing anyway")

time.sleep(1)

tap_by_uiautomator(
    'new UiSelector().description("Ingliz tili B2\nRustam Qoriyev")',
    "Ingliz tili B2 course",
    waiter=wait_long
)

tap_by_id("Test 68.1 A / an vs The articles", "Test 68.1")
tap_by_id("Start", "Start test")

print("Reached the question screen!")

# --- CAPTURE STEP 1: correct answer / Next button screen ---
input("\nTap a CORRECT answer manually until you see the 'Nicely done!' screen, then press Enter here...")
with open("next_button_screen.xml", "w", encoding="utf-8") as f:
    f.write(driver.page_source)
print("Saved next_button_screen.xml")

result = check_answer_feedback(driver)
print(f"Detected feedback: {result}")

dismiss_feedback_and_continue(driver, wait)

# --- CAPTURE STEP 2: incorrect answer screen ---
input("\nTap a WRONG answer manually until you see the incorrect feedback screen, then press Enter here...")
with open("wrong_answer_screen.xml", "w", encoding="utf-8") as f:
    f.write(driver.page_source)
print("Saved wrong_answer_screen.xml")

result = check_answer_feedback(driver)
print(f"Detected feedback: {result}")

dismiss_feedback_and_continue(driver, wait)

print("\nDone capturing both screens.")

# driver.quit() . 