# All UI locators in one place. If the app updates and something breaks,
# fix it here. (Named "locators" not "selectors" — a selectors.py would
# shadow Python's stdlib selectors module and break subprocess/asyncio.)
from appium.webdriver.common.appiumby import AppiumBy

PROGRAM_CERTIFICATE = (AppiumBy.ACCESSIBILITY_ID, "2+6 Program Certificate")
GET_CERTIFICATE = (AppiumBy.ACCESSIBILITY_ID, "Get certificate")
START_TEST = (AppiumBy.ACCESSIBILITY_ID, "Start")
# On the "You must study the lessons in sequence!" reminder dialog
NEXT_LESSON = (AppiumBy.ACCESSIBILITY_ID, "Next lesson")

# Class-unrestricted (//*) because on some sheets "Next" is not an
# android.widget.Button.
NEXT_BUTTON = (AppiumBy.XPATH, "//*[@content-desc='Next']")
CONTINUE_BUTTON = (AppiumBy.XPATH, "//android.widget.Button[@content-desc='Continue']")

PROGRESS_BAR = (AppiumBy.CLASS_NAME, "android.widget.ProgressBar")
