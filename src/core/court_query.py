"""
查询模块
"""
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from typing import List, Dict, Optional
from .browser import BrowserManager
from ..utils.logger import setup_logger
from datetime import datetime

logger = setup_logger(__name__)

class QueryManager:
    """查询管理器"""
    
    def __init__(self, browser: BrowserManager):
        """
        初始化查询管理器
        
        Args:
            browser: 浏览器管理器
        """
        self.browser = browser
        self.venue_url = None  # 保存场馆页面URL
    
    def navigate_to_venue(self, venue_name: str = "羽毛球") -> bool:
        """
        导航到指定场馆页面
        
        Args:
            venue_name: 场馆名称（如"羽毛球"）
            
        Returns:
            是否成功进入场馆页面
        """
        try:
            logger.info(f"正在查找场馆: {venue_name}")
            
            # 等待页面加载完成
            time.sleep(2)
            
            # 多种方式查找羽毛球场馆元素
            selectors = [
                # 方式1: 通过data-v属性和文本内容
                f"//dd[@data-v-30e22189][contains(text(), '{venue_name}')]",
                # 方式2: 通过title属性
                f"//dt[@data-v-30e22189][@title='学院路校区-{venue_name}馆']",
                # 方式3: 通过包含羽毛球的任何元素
                f"//*[contains(text(), '{venue_name}')][@data-v-30e22189]",
                # 方式4: 更宽泛的查找
                f"//dl[@data-v-30e22189]//dd[contains(text(), '{venue_name}')]"
            ]
            
            venue_element = None
            used_selector = None
            
            for selector in selectors:
                try:
                    logger.debug(f"尝试选择器: {selector}")
                    elements = self.browser.driver.find_elements(By.XPATH, selector)
                    
                    # 找到第一个可见的元素
                    for elem in elements:
                        if elem.is_displayed():
                            venue_element = elem
                            used_selector = selector
                            logger.info(f"找到场馆元素: {selector}")
                            logger.debug(f"元素文本: {elem.text}")
                            break
                    
                    if venue_element:
                        break
                        
                except NoSuchElementException:
                    logger.debug(f"选择器 {selector} 未找到元素")
                    continue
            
            # 如果上述方法都失败，尝试查找所有包含"羽毛球"的元素
            if not venue_element:
                logger.info("使用备用方案：查找所有包含'羽毛球'的元素")
                all_elements = self.browser.driver.find_elements(By.XPATH, "//*[contains(text(), '羽毛球')]")
                logger.info(f"找到 {len(all_elements)} 个包含'羽毛球'的元素")
                
                for idx, elem in enumerate(all_elements):
                    try:
                        is_displayed = elem.is_displayed()
                        text = elem.text if elem.text else ""
                        tag_name = elem.tag_name
                        logger.debug(f"元素 {idx}: 标签={tag_name}, 可见={is_displayed}, 文本={text}")
                        
                        # 优先选择可点击的元素
                        if is_displayed and (tag_name in ['dd', 'dt', 'a', 'div', 'dl']):
                            venue_element = elem
                            logger.info(f"通过备用方案找到元素: {tag_name}, 文本={text}")
                            break
                    except Exception as e:
                        logger.debug(f"检查元素 {idx} 失败: {e}")
                        continue
            
            if not venue_element:
                logger.error(f"未找到场馆: {venue_name}")
                self.browser.driver.save_screenshot("venue_not_found.png")
                logger.info("已保存截图到 venue_not_found.png")
                return False
            
            # 获取元素信息用于调试
            try:
                logger.info(f"场馆元素信息:")
                logger.info(f"  - 标签: {venue_element.tag_name}")
                logger.info(f"  - 文本: {venue_element.text}")
                logger.info(f"  - 位置: {venue_element.location}")
                logger.info(f"  - 大小: {venue_element.size}")
            except Exception as e:
                logger.warning(f"获取元素信息失败: {e}")
            
            # 滚动到元素位置
            self.browser.driver.execute_script("arguments[0].scrollIntoView(true);", venue_element)
            time.sleep(0.5)
            
            # 点击元素
            logger.info(f"正在点击场馆: {venue_name}")
            try:
                # 尝试普通点击
                self.browser.driver.execute_script("arguments[0].click();", venue_element)
            except Exception as e:
                logger.warning(f"点击场馆失败: {e}")
            
            # 等待页面跳转
            time.sleep(1)
            
            # 检查是否成功进入场馆页面
            current_url = self.browser.driver.current_url
            logger.info(f"当前URL: {current_url}")
            
            # 保存场馆页面URL
            self.venue_url = current_url
            
            # 检查页面标题或特定元素来确认是否成功
            page_title = self.browser.driver.title
            logger.info(f"页面标题: {page_title}")
            
            # 如果URL发生了变化或页面标题包含场馆信息，认为成功
            if 'venue' in current_url or venue_name in page_title:
                logger.info(f"成功进入{venue_name}场馆页面")
                return True
            else:
                logger.warning(f"可能未成功进入场馆页面，当前标题: {page_title}")
                self.browser.driver.save_screenshot("venue_page_check.png")
                # 即使不确定，也返回True继续执行
                return True
        except Exception as e:
            logger.error(f"导航到场馆失败: {e}", exc_info=True)
            self.browser.driver.save_screenshot("navigate_venue_error.png")
            return False
    
    def parse_court_availability(self) -> Dict[str, List[str]]:
        """
        解析当前页面的场地可用性
        
        Returns:
            字典，格式：{'场馆名称': ['可用时间段1', '可用时间段2', ...]}
            例如：{'训练馆3号': ['08:00-09:00'], '训练馆4号': ['08:00-09:00', '10:00-11:00']}
        """
        try:
            logger.info("开始解析场地可用性...")
            # 查找表格
            table = self.browser.driver.find_element(
                By.CSS_SELECTOR, 
                "div.spaceTable table[data-v-6fb750cc]"
            )
            
            # 获取表头（场馆名称）
            court_names = []
            header_cells = table.find_elements(By.XPATH, ".//thead//td")
            
            for cell in header_cells[1:]:  # 跳过第一个"时间段"列
                try:
                    # 提取场馆名称
                    court_name = cell.text.strip()
                    # 移除可能的箭头符号
                    court_name = court_name.replace('→', '').strip()
                    if court_name:
                        court_names.append(court_name)
                        logger.debug(f"找到场馆: {court_name}")
                except Exception as e:
                    logger.warning(f"解析场馆名称失败: {e}")
                    court_names.append(f"未知场馆{len(court_names)+1}")
            
            logger.info(f"共找到 {len(court_names)} 个场馆: {court_names}")
            
            # 初始化结果字典
            availability = {name: [] for name in court_names}
            
            # 获取所有行
            rows = table.find_elements(By.XPATH, ".//tbody/tr")
            logger.info(f"共找到 {len(rows)} 个时间段")
            
            for row in rows:
                try:
                    # 获取时间段
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) == 0:
                        continue
                    
                    # 第一列是时间段
                    time_slot = cells[0].text.strip()
                    logger.debug(f"处理时间段: {time_slot}")
                    
                    # 遍历每个场馆的状态
                    for idx, cell in enumerate(cells[1:]):  # 跳过时间段列
                        if idx >= len(court_names):
                            break
                        
                        court_name = court_names[idx]
                        
                        try:
                            # 查找状态元素
                            # 可用场地：class包含"free"，显示价格
                            # 已售场地：class包含"reserved"，显示"已售"
                            status_div = cell.find_element(By.CSS_SELECTOR, "div.reserveBlock")
                            class_attr = status_div.get_attribute("class")
                            
                            # 判断是否可用
                            if "free" in class_attr:
                                # 可用场地
                                price_elem = status_div.find_element(By.TAG_NAME, "p")
                                price = price_elem.text.strip()
                                availability[court_name].append(time_slot)
                                logger.debug(f"  {court_name} - {time_slot}: 可用 ({price})")
                            elif "reserved" in class_attr:
                                # 已售
                                logger.debug(f"  {court_name} - {time_slot}: 已售")
                            else:
                                logger.debug(f"  {court_name} - {time_slot}: 未知状态 ({class_attr})")
                                
                        except NoSuchElementException:
                            logger.debug(f"  {court_name} - {time_slot}: 无法获取状态")
                            continue
                        
                except Exception as e:
                    logger.warning(f"解析行数据失败: {e}")
                    continue
            
            # 统计结果
            total_available = sum(len(slots) for slots in availability.values())
            logger.info(f"解析完成，共找到 {total_available} 个可用时间段")
            
            # 输出每个场馆的可用情况
            for court_name, slots in availability.items():
                if slots:
                    logger.info(f"  {court_name}: {len(slots)} 个时间段可用 - {slots}")
                else:
                    logger.debug(f"  {court_name}: 无可用时间段")
            
            return availability
            
        except NoSuchElementException as e:
            logger.error(f"未找到表格元素: {e}")
            self.browser.driver.save_screenshot("parse_table_error.png")
            return {}
        except Exception as e:
            logger.error(f"解析场地可用性失败: {e}", exc_info=True)
            self.browser.driver.save_screenshot("parse_availability_error.png")
            return {}
    
    def query_available_courts(self, date: str = None) -> Dict[str, List[str]]:
        """
        查询可用场地（对外接口）
        
        Args:
            date: 日期（格式：YYYY-MM-DD），如果为None则查询当前页面
            
        Returns:
            可用场地字典
        """
        logger.info(f"查询可用场地: 日期={date if date else '当前页面'}")
        
        try:
            tag = False
            if date:
                tag = self.select_date(date)
            if tag:
                # 解析当前页面的场地可用性
                return self.parse_court_availability()
            logger.error(f"查询结果为空", exc_info=True)
            return {}
        except Exception as e:
            logger.error(f"查询失败: {e}", exc_info=True)
            return {}
    
    def select_date(self, date: str) -> bool:
        """
        选择日期
        
        Args:
            date: 日期（格式：YYYY-MM-DD）
            
        Returns:
            是否选择成功
        """
        try:
            logger.info(f"选择日期: {date}")
            # 实现日期选择逻辑
            if not date:
                logger.warning("日期参数为空，跳过日期选择")
                return False
            
            # 解析日期格式 YYYY-MM-DD
            try:
                date_obj = datetime.strptime(date, "%Y-%m-%d")
                # 格式化为 "12月06日" 格式
                target_date_text = f"{date_obj.month}月{date_obj.day:02d}日"
                logger.info(f"目标日期文本: {target_date_text}")
            except ValueError as e:
                logger.error(f"日期格式错误，应为YYYY-MM-DD: {e}")
                return False
            # 查找所有日期选择框中的日期元素
            try:
                date_elements = self.browser.driver.find_elements(
                    By.CSS_SELECTOR,
                    "div.date_box span[data-v-6fb750cc]"
                )
                
                logger.info(f"找到 {len(date_elements)} 个日期元素")
                
                # 遍历查找匹配的日期
                target_element = None
                for elem in date_elements:
                    try:
                        elem_text = elem.text.strip()
                        logger.debug(f"检查日期元素: {elem_text}")
                        
                        if target_date_text in elem_text:
                            target_element = elem
                            logger.info(f"找到目标日期: {elem_text}")
                            break
                    except Exception as e:
                        logger.debug(f"读取日期元素失败: {e}")
                        continue
                
                if not target_element:
                    logger.error(f"未找到日期 {target_date_text}")
                    self.browser.driver.save_screenshot("date_not_found.png")
                    return False
                
                # 滚动到元素位置
                self.browser.driver.execute_script("arguments[0].scrollIntoView(true);", target_element)
                # time.sleep(0.5)
                
                # 点击日期元素
                logger.info(f"点击日期: {target_date_text}")
                try:
                    self.browser.driver.execute_script("arguments[0].click();", target_element)
                except Exception as e:
                    logger.warning(f"选择日期失败: {e}")
                
                # 等待页面刷新
                time.sleep(0.3)
                logger.info("日期选择完成，页面已刷新")
                
            except NoSuchElementException:
                logger.error("未找到日期选择框")
                self.browser.driver.save_screenshot("date_box_not_found.png")
                return False
            return True
        except Exception as e:
            logger.error(f"选择日期失败: {e}", exc_info=True)
            return False