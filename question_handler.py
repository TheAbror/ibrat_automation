import time

from selenium.webdriver.support import expected_conditions as EC

import locators as loc


def get_screen_type(driver):
    continue_btns = driver.find_elements(*loc.CONTINUE_BUTTON)
    return "ordering" if continue_btns else "multiple_choice"


def get_question_text(driver):
    els = driver.find_elements(*loc.QUESTION_TEXT)
    return els[0].get_attribute("content-desc") if els else None


def check_answer_feedback(driver):
    if driver.find_elements(*loc.NICELY_DONE):
        return "correct"
    if driver.find_elements(*loc.INCORRECT_ANSWER):
        return "incorrect"
    return "unknown"


def wait_for_feedback(driver, timeout=10):
    end_time = time.time() + timeout
    while time.time() < end_time:
        result = check_answer_feedback(driver)
        if result != "unknown":
            return result
        time.sleep(0.3)
    return "unknown"


def dismiss_feedback_and_continue(driver, wait):
    next_btn = wait.until(EC.element_to_be_clickable(loc.NEXT_BUTTON))
    next_btn.click()
    print("Tapped: Next")
    time.sleep(1.5)


def answer_multiple_choice(driver, wait, answer_bank_mc):
    question = get_question_text(driver)
    correct = answer_bank_mc.get(question)

    if not correct:
        print(f"NO ANSWER FOUND for: {question}")
        return False

    options = driver.find_elements(*loc.ALL_BUTTONS)
    for opt in options:
        if opt.get_attribute("content-desc").strip().lower() == correct.strip().lower():
            opt.click()
            time.sleep(1)
            break
    else:
        print(f"Answer '{correct}' not found among options")
        return False

    result = wait_for_feedback(driver)
    print(f"Result: {result} | Question: {question}")
    dismiss_feedback_and_continue(driver, wait)
    return True


def answer_ordering(driver, wait, answer_bank_ordering):
    prompt = get_question_text(driver)
    sequence = answer_bank_ordering.get(prompt)

    if not sequence:
        print(f"NO ORDER FOUND for: {prompt}")
        return False

    for word in sequence:
        chips = driver.find_elements(*loc.NON_CONTINUE_BUTTONS)
        target = next(
            (c for c in chips if c.get_attribute("content-desc").strip() == word.strip()),
            None
        )
        if not target:
            print(f"Chip not found for word: '{word}'")
            return False
        target.click()
        time.sleep(0.4)

    result = wait_for_feedback(driver)
    print(f"Result: {result} | Prompt: {prompt}")
    dismiss_feedback_and_continue(driver, wait)
    return True
