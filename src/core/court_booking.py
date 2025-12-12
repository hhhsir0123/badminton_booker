"""
预订模块
"""
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, ElementClickInterceptedException
from typing import List, Dict, Optional
from .browser import BrowserManager
from .court_query import QueryManager
from ..utils.logger import setup_logger
from ..utils.click_captcha import ClickCaptchaHandler

logger = setup_logger(__name__)


class BookingManager:
    """预订管理器"""
    
    def __init__(self, browser: BrowserManager, query: QueryManager):
        """
        初始化预订管理器
        
        Args:
            browser: 浏览器管理器
            query: 查询管理器
        """
        self.browser = browser
        self.query = query
        self.captcha_handler = ClickCaptchaHandler(browser)
    
    def book_court(self, court_name: str, time_slots: list, partner_names: list) -> bool:
        # step1：点击可用的court
        for time_slot in time_slots:
            success = self.__click_court(court_name, time_slot)
            if success:
                logger.info(f"成功预订 {court_name} 的 {time_slot} 时间段")
            else:
                logger.warning(f"预订 {court_name} 的 {time_slot} 时间段失败，尝试下一个时间段")
        # step2: 选择同伴
        # self.click_partner(partner_names[0])  # 仅选择第一个同伴作为示例
        # Step3：点击提交
        self.__click_submit()
        # step4: 处理点击类的验证码
        if self.captcha_handler._check_click_captcha_exists():
            logger.info("检测到验证码，开始处理...")
            if not self.captcha_handler.handle_click_captcha():
                logger.error("验证码处理失败")
                return False
        return True

    def __click_submit(self) -> bool:
        """
        点击提交按钮
                
        Returns:
            是否成功提交
        """
        logger.info("尝试点击提交按钮")

        try:
            # # 等待提交按钮加载
            # time.sleep(1)
            
            # 查找submit_order_box容器
            submit_box = self.browser.driver.find_element(
                By.CSS_SELECTOR,
                "div.submit_order_box.pc"
            )
            
            # 查找提交按钮
            submit_button = submit_box.find_element(By.CSS_SELECTOR, "div.btn")
            
            # 检查按钮是否可点击（可能有disabled状态）
            button_class = submit_button.get_attribute("class")
            if "disabled" in button_class:
                logger.warning("提交按钮处于禁用状态")
                return False
            
            # 尝试点击提交按钮
            click_success = False
            
            # 方式1: 直接点击
            try:
                self.browser.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", 
                    submit_button
                )
                time.sleep(0.3)
                submit_button.click()
                logger.info("成功点击提交按钮（方式1：直接点击）")
                click_success = True
            except ElementClickInterceptedException as e:
                logger.debug(f"方式1点击失败（被遮挡）: {e}")
            except Exception as e:
                logger.debug(f"方式1点击失败: {e}")
            
            # 方式2: JavaScript点击
            if not click_success:
                try:
                    self.browser.driver.execute_script("arguments[0].click();", submit_button)
                    logger.info("成功点击提交按钮（方式2：JavaScript）")
                    click_success = True
                except Exception as e:
                    logger.debug(f"方式2点击失败: {e}")
            
            if not click_success:
                logger.error("所有点击方式都失败")
                self.browser.driver.save_screenshot("submit_click_failed.png")
                return False
            
            # 等待提交响应
            time.sleep(0.5)
            
            # 检查提交结果（可以根据页面跳转、成功提示等判断）
            try:
                # 检查是否有成功提示
                success_indicators = [
                    "div.success-message",
                    "div.ivu-message-success",
                    "div.ant-message-success"
                ]
                
                for selector in success_indicators:
                    elements = self.browser.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed():
                            logger.info("检测到提交成功提示")
                            return True
            except Exception as e:
                logger.debug(f"检查成功提示时出错: {e}")
            
            logger.info("提交按钮已点击")
            return True
        except NoSuchElementException:
            logger.error("未找到提交按钮")
            self.browser.driver.save_screenshot("submit_button_not_found.png")
            return False
        except Exception as e:
            logger.error(f"点击提交按钮失败: {e}", exc_info=True)
            self.browser.driver.save_screenshot("submit_error.png")
            return False
                            
    def click_partner(self, partner_name: str) -> bool:
        """
        点击同伴按钮
                
        Args:
            partner_name: 同伴名称
                    
        Returns:
            是否成功点击
        """
        logger.info(f"尝试选择同伴: {partner_name}")

        try:
            # 等待buddy-box加载
            time.sleep(1)
            
            # 查找buddy-box容器
            buddy_box = self.browser.driver.find_element(
                By.CSS_SELECTOR,
                "div.buddy-box"
            )
            
            # 查找所有buddy-row
            buddy_rows = buddy_box.find_elements(By.CSS_SELECTOR, "div.buddy-row")
            
            for row in buddy_rows:
                try:
                    # 获取该行的文本内容
                    row_text = row.text.strip()
                    logger.debug(f"检查同伴行: {row_text}")
                    
                    # 检查是否匹配同伴名称
                    if partner_name in row_text:
                        # 尝试点击该行（可能整行可点击）
                        try:
                            self.browser.driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center'});", 
                                row
                            )
                            time.sleep(0.3)
                            row.click()
                            logger.info(f"成功选择同伴: {partner_name}")
                            return True
                        except Exception as e:
                            logger.debug(f"点击行失败，尝试查找按钮: {e}")
                            
                            # 查找行内的按钮或可点击元素
                            clickable_elements = row.find_elements(
                                By.CSS_SELECTOR,
                                "button, div[onclick], span[onclick], a"
                            )
                            
                            for element in clickable_elements:
                                try:
                                    self.browser.driver.execute_script(
                                        "arguments[0].scrollIntoView({block: 'center'});",
                                        element
                                    )
                                    time.sleep(0.3)
                                    element.click()
                                    logger.info(f"成功选择同伴: {partner_name}")
                                    return True
                                except Exception as e2:
                                    logger.debug(f"点击元素失败: {e2}")
                                    continue
                except Exception as e:
                    logger.debug(f"处理同伴行时出错: {e}")
                    continue
            
            logger.warning(f"未找到同伴: {partner_name}")
            return False
            
        except NoSuchElementException:
            logger.error("未找到buddy-box元素")
            self.browser.driver.save_screenshot("buddy_box_not_found.png")
            return False
        except Exception as e:
            logger.error(f"选择同伴失败: {e}", exc_info=True)
            self.browser.driver.save_screenshot("partner_click_error.png")
            return False
                
    def __click_court(self, court_name: str, time_slot: str) -> bool:
        """
        预订场地
        
        Args:
            court_name: 场地名称（如"训练馆3号"）
            time_slot: 时间段（如"08:00-09:00"）
            
        Returns:
            是否成功点击预订按钮
        """
        logger.info(f"尝试预订: {court_name}, {time_slot}")
        
        try:
            # 等待表格加载
            # time.sleep(0.5)
            
            # 查找表格
            table = self.browser.driver.find_element(
                By.CSS_SELECTOR, 
                "div.spaceTable table[data-v-6fb750cc]"
            )
            
            # 1. 找到场地名称对应的列索引
            court_column_index = self._find_court_column_index(table, court_name)
            if court_column_index is None:
                logger.error(f"未找到场地: {court_name}")
                return False
            
            logger.info(f"场地 '{court_name}' 位于第 {court_column_index} 列")
            
            # 2. 找到时间段对应的行
            target_row = self._find_time_slot_row(table, time_slot)
            if target_row is None:
                logger.error(f"未找到时间段: {time_slot}")
                return False
            
            logger.info(f"时间段 '{time_slot}' 找到对应行")
            
            # 3. 定位到具体的单元格
            cells = target_row.find_elements(By.TAG_NAME, "td")
            if court_column_index >= len(cells):
                logger.error(f"列索引超出范围: {court_column_index} >= {len(cells)}")
                return False
            
            target_cell = cells[court_column_index]
            
            # 4. 检查是否可预订（是否为"free"状态）
            try:
                status_div = target_cell.find_element(By.CSS_SELECTOR, "div.reserveBlock")
                class_attr = status_div.get_attribute("class")
                
                if "reserved" in class_attr:
                    logger.warning(f"{court_name} 在 {time_slot} 已被预订")
                    return False
                
                if "free" not in class_attr:
                    logger.warning(f"{court_name} 在 {time_slot} 状态未知: {class_attr}")
                    return False
                
                logger.info(f"场地可预订，准备点击...")
                
                # 5. 点击预订按钮
                # 尝试多种方式点击
                click_success = False
                
                # 方式1: 点击整个div
                try:
                    self.browser.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", status_div)
                    # time.sleep(0.3)
                    status_div.click()
                    logger.info("成功点击预订按钮（方式1：点击div）")
                    click_success = True
                except Exception as e:
                    logger.debug(f"方式1点击失败: {e}")
                
                # 方式2: JavaScript点击
                if not click_success:
                    try:
                        self.browser.driver.execute_script("arguments[0].click();", status_div)
                        logger.info("成功点击预订按钮（方式2：JavaScript）")
                        click_success = True
                    except Exception as e:
                        logger.debug(f"方式2点击失败: {e}")
                
                # 方式3: 点击单元格
                if not click_success:
                    try:
                        self.browser.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_cell)
                        # time.sleep(0.3)
                        target_cell.click()
                        logger.info("成功点击预订按钮（方式3：点击单元格）")
                        click_success = True
                    except Exception as e:
                        logger.debug(f"方式3点击失败: {e}")
                
                if not click_success:
                    logger.error("所有点击方式都失败")
                    self.browser.driver.save_screenshot("click_failed.png")
                    return False
                
                # 6. 等待页面响应
                # time.sleep(0.3)
                
                # 7. 检查是否弹出预订确认框或跳转到预订页面
                # TODO: 根据实际页面行为，可能需要处理弹窗、确认等
                current_url = self.browser.driver.current_url
                logger.info(f"点击后URL: {current_url}")
                
                # 检查是否有弹窗
                try:
                    # 可能的弹窗选择器
                    modal_selectors = [
                        "div.ivu-modal",
                        "div.ant-modal",
                        "div.el-dialog",
                        "div[role='dialog']"
                    ]
                    
                    for selector in modal_selectors:
                        modals = self.browser.driver.find_elements(By.CSS_SELECTOR, selector)
                        for modal in modals:
                            if modal.is_displayed():
                                logger.info(f"检测到弹窗: {selector}")
                                # 这里可以添加确认预订的逻辑
                                # 例如：点击"确认"按钮
                                break
                except Exception as e:
                    logger.debug(f"检查弹窗时出错: {e}")
                
                logger.info(f"成功点击 {court_name} 的 {time_slot} 时间段")
                return True
                
            except NoSuchElementException:
                logger.error(f"未找到状态元素")
                return False
            
        except NoSuchElementException as e:
            logger.error(f"未找到表格元素: {e}")
            self.browser.driver.save_screenshot("table_not_found.png")
            return False
        except Exception as e:
            logger.error(f"预订失败: {e}", exc_info=True)
            self.browser.driver.save_screenshot("booking_error.png")
            return False
    
    def _find_court_column_index(self, table, court_name: str) -> Optional[int]:
        """
        查找场地名称对应的列索引
        
        Args:
            table: 表格元素
            court_name: 场地名称
            
        Returns:
            列索引（从0开始，0是时间列），如果未找到返回None
        """
        try:
            header_cells = table.find_elements(By.XPATH, ".//thead//td")
            
            for idx, cell in enumerate(header_cells):
                cell_text = cell.text.strip()
                # 移除可能的箭头符号
                cell_text = cell_text.replace('→', '').strip()
                
                logger.debug(f"列 {idx}: {cell_text}")
                
                # 完全匹配或包含匹配
                if cell_text == court_name or court_name in cell_text:
                    return idx
            
            return None
            
        except Exception as e:
            logger.error(f"查找列索引失败: {e}")
            return None
    
    def _find_time_slot_row(self, table, time_slot: str) -> Optional:
        """
        查找时间段对应的行元素
        
        Args:
            table: 表格元素
            time_slot: 时间段（如"08:00-09:00"）
            
        Returns:
            行元素，如果未找到返回None
        """
        try:
            rows = table.find_elements(By.XPATH, ".//tbody/tr")
            
            for row in rows:
                # 获取第一个单元格（时间段）
                first_cell = row.find_element(By.TAG_NAME, "td")
                row_time_slot = first_cell.text.strip()
                
                logger.debug(f"检查行: {row_time_slot}")
                
                # 完全匹配
                if row_time_slot == time_slot:
                    return row
                
                # 宽松匹配（去除空格）
                if row_time_slot.replace(' ', '') == time_slot.replace(' ', ''):
                    return row
            
            return None
            
        except Exception as e:
            logger.error(f"查找时间段行失败: {e}")
            return None
    
    def auto_book(self, venue_type: str, date: str, time_slots: List[str], 
                  preferred_courts: List[int]) -> bool:
        """
        自动预订（按优先级尝试）
        
        Args:
            venue_type: 场地类型
            date: 日期
            time_slots: 时间段列表（按优先级排序）
            preferred_courts: 优先场地编号列表
            
        Returns:
            是否预订成功
        """
        for time_slot in time_slots:
            available_courts = self.query.query_available_courts(venue_type, date, time_slot)
            
            # 按优先级尝试预订
            for court_num in preferred_courts:
                if any(court['number'] == court_num for court in available_courts):
                    if self.book_court(venue_type, date, time_slot, court_num):
                        return True
            
            # 如果优先场地都不可用，尝试其他可用场地
            for court in available_courts:
                if self.book_court(venue_type, date, time_slot, court['number']):
                    return True
        
        logger.warning("所有时间段和场地都预订失败")
        return False