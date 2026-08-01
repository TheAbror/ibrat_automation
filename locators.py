# All UI locators in one place. If the app updates and something breaks,
# fix it here. (Named "locators" not "selectors" — a selectors.py would
# shadow Python's stdlib selectors module and break subprocess/asyncio.)
from appium.webdriver.common.appiumby import AppiumBy

PROGRAM_CERTIFICATE = (AppiumBy.ACCESSIBILITY_ID, "2+6 Program Certificate")
GET_CERTIFICATE = (AppiumBy.ACCESSIBILITY_ID, "Get certificate")
START_TEST = (AppiumBy.ACCESSIBILITY_ID, "Start")

# Accessibility ID matches any widget class. On the incorrect-answer sheet
# "Next" is not an android.widget.Button, so a class-restricted XPath misses it.
NEXT_BUTTON = (AppiumBy.ACCESSIBILITY_ID, "Next")
CONTINUE_BUTTON = (AppiumBy.XPATH, "//android.widget.Button[@content-desc='Continue']")

NICELY_DONE = (AppiumBy.XPATH, "//*[@content-desc='Nicely done!']")
INCORRECT_ANSWER = (AppiumBy.XPATH, "//*[@content-desc='Incorrect Answer!']")

QUESTION_TEXT = (AppiumBy.XPATH, "//android.view.View[@content-desc!='']")
ALL_BUTTONS = (AppiumBy.CLASS_NAME, "android.widget.Button")
NON_CONTINUE_BUTTONS = (AppiumBy.XPATH, "//android.widget.Button[@content-desc!='Continue']")

PROGRESS_BAR = (AppiumBy.CLASS_NAME, "android.widget.ProgressBar")
