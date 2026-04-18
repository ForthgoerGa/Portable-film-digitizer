/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body with UART Motor Control and Panic Stop
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
void DWT_Init(void);
void delay_us(uint32_t us);
void Process_Command(char* buffer);
/* USER CODE END PFP */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN PV */
uint8_t rx_byte;                // Single byte received from UART
char rx_buffer[64];             // Buffer to store the incoming string
uint8_t rx_index = 0;           // Current position in buffer
volatile uint8_t cmd_ready = 0; // Flag set when ';' is received
volatile uint8_t stop_flag = 0; // Emergency stop flag
/* USER CODE END PV */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* MCU Configuration--------------------------------------------------------*/
  HAL_Init();
  SystemClock_Config();

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART2_UART_Init();
  MX_TIM3_Init();

  /* USER CODE BEGIN 2 */
  DWT_Init();

  // Start the UART Interrupt to listen for the first byte
  HAL_UART_Receive_IT(&huart2, &rx_byte, 1);

  // Optional: Boot message to confirm serial is working
  char boot_msg[] = "Motor Controller Ready. Press 'q' to panic stop.\r\n";
  HAL_UART_Transmit(&huart2, (uint8_t*)boot_msg, strlen(boot_msg), 100);
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    if (cmd_ready)
    {
      Process_Command(rx_buffer);
      cmd_ready = 0; // Reset flag after processing
    }
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief UART Receive Callback - Triggered every time a byte arrives
  */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART2)
  {
    // --- PANIC BUTTON CHECK ---
    if (rx_byte == 'q')
    {
      stop_flag = 1;
      char panic_alert[] = "\r\n!!! PANIC STOP TRIGGERED !!!\r\n";
      HAL_UART_Transmit(huart, (uint8_t*)panic_alert, strlen(panic_alert), 50);
    }

    // --- DEBUG ECHO ---
    HAL_UART_Transmit(huart, &rx_byte, 1, 10);

    // --- PROTOCOL PARSING ---
    if (rx_byte == ';') // End of command
    {
      rx_buffer[rx_index] = '\0'; // Null terminate string
      cmd_ready = 1;
      rx_index = 0;
    }
    else if (rx_byte == '$') // Start of command
    {
      rx_index = 0;
    }
    else if (rx_index < 63)
    {
      rx_buffer[rx_index++] = rx_byte;
    }

    // Restart interrupt to receive next byte
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
  }
}

/**
  * @brief Logic to parse and execute commands
  */
void Process_Command(char* buffer)
{
  // Send an acknowledgment back to the terminal
  char msg[64];
  sprintf(msg, "Executing: %s\r\n", buffer);
  HAL_UART_Transmit(&huart2, (uint8_t*)msg, strlen(msg), 100);

  // Format expected: "MX:1000" (Move X)
  if (strncmp(buffer, "MX:", 3) == 0)
  {
    int32_t steps = atoi(&buffer[3]);
    uint32_t speed_delay = 800;  // Start slow for every move
    uint32_t target_delay = 100; // Final speed

    stop_flag = 0; // Reset stop flag before starting move

    // Set Direction
    if (steps > 0) {
      HAL_GPIO_WritePin(DIR_X_PIN_GPIO_Port, DIR_X_PIN_Pin, GPIO_PIN_SET);
    } else {
      HAL_GPIO_WritePin(DIR_X_PIN_GPIO_Port, DIR_X_PIN_Pin, GPIO_PIN_RESET);
      steps = -steps; // Make steps positive for the loop
    }

    // Move loop with Ramp and Panic Check
    for (int32_t i = 0; i < steps; i++)
    {
      // Check if 'q' was pressed during the move
      if (stop_flag)
      {
        char stop_ack[] = "Motor Aborted.\r\n";
        HAL_UART_Transmit(&huart2, (uint8_t*)stop_ack, strlen(stop_ack), 50);
        break;
      }

      HAL_GPIO_WritePin(STEP_X_PIN_GPIO_Port, STEP_X_PIN_Pin, GPIO_PIN_SET);
      delay_us(speed_delay);
      HAL_GPIO_WritePin(STEP_X_PIN_GPIO_Port, STEP_X_PIN_Pin, GPIO_PIN_RESET);
      delay_us(speed_delay);

      // Simple ramp down of delay (increase speed)
      if (speed_delay > target_delay && i % 2 == 0) {
        speed_delay--;
      }
    }

    stop_flag = 0; // Ensure flag is clear for next command
  }
}

/**
  * @brief System Clock Configuration
  */
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

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;
  HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2);
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

void Error_Handler(void) {
  __disable_irq();
  while (1) {}
}
