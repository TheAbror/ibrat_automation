from appium import webdriver
from appium.options.android import UiAutomator2Options
import time

options = UiAutomator2Options()
options.platform_name = "Android"
options.automation_name = "UiAutomator2"
options.device_name = "ZY22GTXB9R"
options.app_package = "uz.ibrat.farzandlari"
options.app_activity = ".MainActivity"
options.no_reset = True

driver = webdriver.Remote("http://127.0.0.1:4723", options=options)

# Dismiss whatever panel/menu is currently open
driver.press_keycode(4)  # Android BACK button
time.sleep(1)
driver.press_keycode(4)  # press again just in case
time.sleep(1)

# Force-launch the app explicitly (more reliable than relying on session startup)
driver.activate_app("uz.ibrat.farzandlari")
time.sleep(3)

print("current_activity:", driver.current_activity)
print(driver.page_source[:2000])

driver.quit()