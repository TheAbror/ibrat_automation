from appium import webdriver
from appium.options.android import UiAutomator2Options

options = UiAutomator2Options()
options.platform_name = "Android"
options.automation_name = "UiAutomator2"
options.device_name = "ZY22GTXB9R"
options.app_package = "uz.ibrat.farzandlari"
options.app_activity = ".MainActivity"
options.no_reset = True

driver = webdriver.Remote("http://127.0.0.1:4723", options=options)

print("App launched successfully!")
print(driver.current_activity)

import time
time.sleep(5)
driver.quit()
