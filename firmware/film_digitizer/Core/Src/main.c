/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Dual Axis Motor Control (X & Y) via USART1
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "usart.h"
#include "gpio.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
void DWT_Init(void);
void delay_us(uint32_t us);
void Process_Command(char* buffer, UART_HandleTypeDef *huart);
void Step_Motor(GPIO_TypeDef* port, uint16_t pin, uint32_t delay);

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN PV */
uint8_t rx1_byte;
char rx1_buffer[64];
uint8_t rx1_index = 0;

char active_cmd[64];
volatile uint8_t cmd_ready = 0;
volatile uint8_t stop_flag = 0;
UART_HandleTypeDef *active_huart = NULL;

typedef enum {
    MOTOR_IDLE,
    MOTOR_JOG_X_FWD,
    MOTOR_JOG_X_REV,
    MOTOR_JOG_Y_FWD,
    MOTOR_JOG_Y_REV,
    MOTOR_BUSY_API
} MotorState_t;

volatile MotorState_t motor_state = MOTOR_IDLE;

const uint32_t RAMP_START_DELAY = 800;
const uint32_t RAMP_MIN_DELAY = 100;
/* USER CODE END PV */

int main(void)
{
  HAL_Init();
  SystemClock_Config();
  MX_GPIO_Init();
  MX_USART1_UART_Init();

  /* USER CODE BEGIN 2 */
  DWT_Init();
  HAL_UART_Receive_IT(&huart1, &rx1_byte, 1);

  uint32_t current_jog_delay = RAMP_START_DELAY;
  uint32_t ramp_counter = 0;
  uint32_t last_heartbeat = 0;

  char boot_msg[] = "\r\n--- Dual Axis Interface (X & Y) ---\r\n"
                    "A/D: Jog X | W/S: Jog Y | Q: Stop\r\n"
                    "API: $MX:steps; or $MY:steps;\r\n";
  HAL_UART_Transmit(&huart1, (uint8_t*)boot_msg, strlen(boot_msg), 100);
  /* USER CODE END 2 */

  while (1)
  {
    // Status LED logic
    if (motor_state != MOTOR_IDLE) {
        HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_SET);
    } else {
        if (HAL_GetTick() - last_heartbeat > 500) {
            HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
            last_heartbeat = HAL_GetTick();
        }
    }

    // 1. Handle Precise API Movements ($MX:1000; or $MY:1000;)
    if (cmd_ready && active_huart != NULL)
    {
      motor_state = MOTOR_BUSY_API;
      Process_Command(active_cmd, active_huart);
      motor_state = MOTOR_IDLE;
      cmd_ready = 0;
    }

    // 2. Dual Axis Jogging Logic
    if (motor_state != MOTOR_IDLE && motor_state != MOTOR_BUSY_API)
    {
      GPIO_TypeDef* step_port;
      uint16_t step_pin;

      // Determine Direction and Pins based on state
      if (motor_state == MOTOR_JOG_X_FWD || motor_state == MOTOR_JOG_X_REV) {
          HAL_GPIO_WritePin(DIR_X_PIN_GPIO_Port, DIR_X_PIN_Pin, (motor_state == MOTOR_JOG_X_FWD) ? GPIO_PIN_RESET : GPIO_PIN_SET);
          step_port = STEP_X_PIN_GPIO_Port;
          step_pin = STEP_X_PIN_Pin;
      } else {
          HAL_GPIO_WritePin(DIR_Y_PIN_GPIO_Port, DIR_Y_PIN_Pin, (motor_state == MOTOR_JOG_Y_FWD) ? GPIO_PIN_RESET : GPIO_PIN_SET);
          step_port = STEP_Y_PIN_GPIO_Port;
          step_pin = STEP_Y_PIN_Pin;
      }

      // Execute Step
      HAL_GPIO_WritePin(step_port, step_pin, GPIO_PIN_SET);
      delay_us(current_jog_delay);
      HAL_GPIO_WritePin(step_port, step_pin, GPIO_PIN_RESET);
      delay_us(current_jog_delay);

      // Simple Ramp
      if (current_jog_delay > RAMP_MIN_DELAY) {
          ramp_counter++;
          if (ramp_counter >= 2) {
              current_jog_delay--;
              ramp_counter = 0;
          }
      }
    }
    else
    {
      current_jog_delay = RAMP_START_DELAY;
      ramp_counter = 0;
    }
  }
}

/**
  * @brief UART Rx Callback
  */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART1) {
    if (rx1_byte == 'q' || rx1_byte == 'Q') {
      stop_flag = 1;
      motor_state = MOTOR_IDLE;
    }
    // X-Axis Jog (A/D)
    else if (rx1_byte == 'd') { motor_state = MOTOR_JOG_X_FWD; stop_flag = 0; }
    else if (rx1_byte == 'a') { motor_state = MOTOR_JOG_X_REV; stop_flag = 0; }
    // Y-Axis Jog (W/S)
    else if (rx1_byte == 'w') { motor_state = MOTOR_JOG_Y_FWD; stop_flag = 0; }
    else if (rx1_byte == 's') { motor_state = MOTOR_JOG_Y_REV; stop_flag = 0; }
    else {
      if (rx1_byte == ';') {
        rx1_buffer[rx1_index] = '\0';
        strcpy(active_cmd, rx1_buffer);
        active_huart = huart;
        cmd_ready = 1;
        rx1_index = 0;
      }
      else if (rx1_byte == '$') { rx1_index = 0; }
      else if (rx1_index < 63) { rx1_buffer[rx1_index++] = rx1_byte; }
    }

    HAL_UART_Transmit(huart, &rx1_byte, 1, 10);
    HAL_UART_Receive_IT(huart, &rx1_byte, 1);
  }
}

/**
  * @brief API Movement Processing for X and Y
  */
void Process_Command(char* buffer, UART_HandleTypeDef *huart)
{
  GPIO_TypeDef* step_port;
  uint16_t step_pin;
  GPIO_TypeDef* dir_port;
  uint16_t dir_pin;

  // Identify Axis
  if (strncmp(buffer, "MX:", 3) == 0) {
      step_port = STEP_X_PIN_GPIO_Port; step_pin = STEP_X_PIN_Pin;
      dir_port = DIR_X_PIN_GPIO_Port;   dir_pin = DIR_X_PIN_Pin;
  } else if (strncmp(buffer, "MY:", 3) == 0) {
      step_port = STEP_Y_PIN_GPIO_Port; step_pin = STEP_Y_PIN_Pin;
      dir_port = DIR_Y_PIN_GPIO_Port;   dir_pin = DIR_Y_PIN_Pin;
  } else {
      return; // Unknown command
  }

  int32_t steps = atoi(&buffer[3]);
  uint32_t speed_delay = RAMP_START_DELAY;
  stop_flag = 0;

  HAL_GPIO_WritePin(dir_port, dir_pin, (steps > 0) ? GPIO_PIN_RESET : GPIO_PIN_SET);
  steps = (steps < 0) ? -steps : steps;

  for (int32_t i = 0; i < steps; i++)
  {
    if (stop_flag) break;
    HAL_GPIO_WritePin(step_port, step_pin, GPIO_PIN_SET);
    delay_us(speed_delay);
    HAL_GPIO_WritePin(step_port, step_pin, GPIO_PIN_RESET);
    delay_us(speed_delay);
    if (speed_delay > RAMP_MIN_DELAY && i % 2 == 0) speed_delay--;
  }

  char msg[128];
  sprintf(msg, "\r\n%s: %s\r\n", stop_flag ? "STOPPED" : "OK", buffer);
  HAL_UART_Transmit(huart, (uint8_t*)msg, strlen(msg), 100);
  stop_flag = 0;
}

void DWT_Init(void) {
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

void delay_us(uint32_t us) {
  uint32_t startTick = DWT->CYCCNT;
  uint32_t delayTicks = us * (SystemCoreClock / 1000000);
  while (DWT->CYCCNT - startTick < delayTicks);
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart) {
    if (huart->Instance == USART1) HAL_UART_Receive_IT(&huart1, &rx1_byte, 1);
}

void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 16;
  RCC_OscInitStruct.PLL.PLLN = 336;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ = 7;
  HAL_RCC_OscConfig(&RCC_OscInitStruct);
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK|RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;
  HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2);
}

void Error_Handler(void) { __disable_irq(); while (1) {} }
